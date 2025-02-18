import json
import os
import boto3
import requests
import ydb
from PIL import Image
import io
import uuid
from typing import Dict

AWS_ACCESS_KEY: str = os.environ["AWS_ACCESS_KEY_ID"]
AWS_SECRET_KEY: str = os.environ["AWS_SECRET_ACCESS_KEY"]
SOURCE_BUCKET: str = os.environ["FROM_BUCKET_NAME"]
DESTINATION_BUCKET: str = os.environ["TO_BUCKET_NAME"]

YDB_ENDPOINT: str = os.environ['YDB_ENDPOINT']
YDB_DATABASE: str = os.environ['YDB_DATABASE']
ydb_driver = ydb.Driver(
    endpoint=f"grpcs://{YDB_ENDPOINT}",
    database=YDB_DATABASE,
    credentials=ydb.iam.MetadataUrlCredentials(),
)
ydb_driver.wait(fail_fast=True, timeout=5)
session_pool = ydb.SessionPool(ydb_driver)


def insert_into_database(session, face_identifier: str, original_image_key: str):
    query = f'INSERT INTO faces(face_key, original_key) VALUES ("{face_identifier}", "{original_image_key}");'
    session.transaction().execute(
        query,
        commit_tx=True,
        settings=ydb.BaseRequestSettings().with_timeout(3).with_operation_timeout(2)
    )


def handler(event, context) -> Dict[str, str]:
    aws_session = boto3.Session(
        aws_access_key_id=AWS_ACCESS_KEY,
        aws_secret_access_key=AWS_SECRET_KEY,
        region_name="ru-central1",
    )

    s3_client = aws_session.client(
        service_name='s3',
        endpoint_url='https://storage.yandexcloud.net'
    )

    message_body = json.loads(event['messages'][0]["details"]["message"]["body"])
    image_key = message_body['img_key']
    crop_coordinates = message_body["coordinates"]

    presigned_url = s3_client.generate_presigned_url("get_object",
                                                     Params={"Bucket": SOURCE_BUCKET, "Key": image_key},
                                                     ExpiresIn=100, )

    try:
        response = requests.get(presigned_url)
        response.raise_for_status()

        with Image.open(io.BytesIO(response.content)) as img:
            cropped_img = img.crop(
                (int(crop_coordinates[0]["x"]), int(crop_coordinates[0]["y"]),
                 int(crop_coordinates[2]["x"]), int(crop_coordinates[2]["y"])))

            with io.BytesIO() as output_buffer:
                cropped_img.save(output_buffer, 'JPEG')
                output_buffer.seek(0)

                face_image_key = f"face_{uuid.uuid4()}.jpeg"
                s3_client.put_object(Bucket=DESTINATION_BUCKET, Key=face_image_key,
                                     Body=output_buffer, ContentType="image/jpeg")
                session_pool.retry_operation_sync(insert_into_database, None, face_image_key, image_key)

    except Exception as e:
        print(f"Error during image processing: {e}")
        return {'statusCode': 500, 'body': str(e)}

    return {'statusCode': 200}
