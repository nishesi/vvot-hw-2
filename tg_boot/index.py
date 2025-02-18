import os
import json
import requests
from typing import Any, Dict, List, Optional
import ydb
import ydb.iam
import boto3

AWS_ACCESS_KEY_ID: Optional[str] = os.environ.get("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY: Optional[str] = os.environ.get("AWS_SECRET_ACCESS_KEY")
API_GATEWAY: str = f"https://{os.environ.get('API_GATEWAY')}"
TELEGRAM_BOT_TOKEN: Optional[str] = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_API_URL: str = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
BUCKET_NAME: Optional[str] = os.environ.get("BUCKET_NAME")
FUNC_RESPONSE: Dict[str, Any] = {'statusCode': 200, 'body': ''}

driver = ydb.Driver(
    endpoint=f"grpcs://{os.environ.get('YDB_ENDPOINT')}",
    database=os.environ.get('YDB_DATABASE'),
    credentials=ydb.iam.MetadataUrlCredentials(),
)
driver.wait(fail_fast=True, timeout=5)
pool = ydb.SessionPool(driver)


def get_faces_without_name(session: ydb.Session):
    query = 'SELECT face_key FROM faces WHERE face_name IS NULL;'
    return session.transaction().execute(
        query,
        commit_tx=True,
        settings=ydb.BaseRequestSettings().with_timeout(3).with_operation_timeout(2)
    )


def update_face_name(session: ydb.Session, new_name: str, face_key: str):
    query = f'UPDATE faces SET face_name="{new_name}" WHERE face_key="{face_key}";'
    session.transaction().execute(
        query,
        commit_tx=True,
        settings=ydb.BaseRequestSettings().with_timeout(3).with_operation_timeout(2)
    )


def send_message(chat_id: int, text: str, message_id: int):
    reply_message = {'chat_id': chat_id, 'text': text, 'reply_to_message_id': message_id}
    requests.post(url=f'{TELEGRAM_API_URL}/sendMessage', json=reply_message)


def send_photo(chat_id: int, img_key: str, message_id: int):
    img_url = f"{API_GATEWAY}/?face={img_key}"
    reply_message = {'chat_id': chat_id, 'photo': img_url, 'caption': img_key, 'reply_to_message_id': message_id}
    requests.post(url=f'{TELEGRAM_API_URL}/sendPhoto', json=reply_message)


def send_media_group(chat_id, image_keys, message_id):
    session = boto3.Session(
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
        region_name="ru-central1",
    )
    s3 = session.client('s3', endpoint_url='https://storage.yandexcloud.net')

    media_group = []
    for img_key in image_keys:
        img_key = img_key.strip("b'").strip("'")
        img_url = s3.generate_presigned_url("get_object",
                                            Params={"Bucket": BUCKET_NAME, "Key": img_key},
                                            ExpiresIn=300,)  # URL действителен в течение 5 минут
        media_group.append({'type': 'photo', 'media': img_url})

    reply_message = {
        'chat_id': chat_id,
        'media': json.dumps(media_group),
        'reply_to_message_id': message_id
    }
    requests.post(url=f'{TELEGRAM_API_URL}/sendMediaGroup', json=reply_message)


def get_res(session: ydb.Session, sql_query: str):
    try:
        return session.transaction().execute(
            sql_query,
            commit_tx=True,
            settings=ydb.BaseRequestSettings().with_timeout(3).with_operation_timeout(2)
        )
    except Exception as e:
        print(f"Ошибка выполнения SQL-запроса: {e}")
        return None


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    body = json.loads(event['body'])
    message = body.get('message', {})

    if not TELEGRAM_BOT_TOKEN or 'message' not in body:
        return FUNC_RESPONSE

    chat_id = message['chat']['id']
    message_id = message['message_id']
    text = message.get('text', '')

    if "/start" in text:
        send_message(chat_id, 'Выберите команду /getface или /find {name}', message_id)
    elif "/getface" in text:
        faces = pool.retry_operation_sync(get_faces_without_name)
        if not faces or not faces[0].rows:
            send_message(chat_id, 'Все изображения имеют названия', message_id)
        else:
            face_key = faces[0].rows[0]['face_key'].decode('utf-8')
            send_photo(chat_id, face_key, message_id)
    elif 'reply_to_message' in message and 'photo' in message['reply_to_message']:
        new_name = text
        face_key = message['reply_to_message']['caption']
        pool.retry_operation_sync(update_face_name, None, new_name, face_key)
        send_message(chat_id, f'Новое название изображения - {new_name}', message_id)
    elif "/find" in text:
        name = text.split(" ")[1] if len(text.split(" ")) > 1 else ''
        if not name:
            send_message(chat_id, 'Введите название', message_id)
        else:
            result = pool.retry_operation_sync(lambda s: get_res(s, f"SELECT * FROM faces WHERE face_name='{name}';"))
            if not result or not result[0].rows:
                send_message(chat_id, f'Фотографии с {name} не найдены', message_id)
            else:
                image_keys = [row['original_key'].decode('utf-8') for row in result[0].rows]
                send_media_group(chat_id, image_keys, message_id)
    else:
        send_message(chat_id, 'Ошибка', message_id)

    return FUNC_RESPONSE
