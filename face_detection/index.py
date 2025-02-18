import requests
import boto3
import os
import base64
import json

VISION_API_URL = "https://vision.api.cloud.yandex.net/vision/v1/batchAnalyze"
YANDEX_STORAGE_ENDPOINT_URL = 'https://storage.yandexcloud.net'
YANDEX_QUEUE_ENDPOINT_URL = 'https://message-queue.api.cloud.yandex.net'
AWS_REGION = 'ru-central1'

FOLDER_ID = os.environ.get("FOLDER_ID")
AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY")
QUEUE_URL = os.environ.get("QUEUE_URL")


def create_vision_request_body(image_content: str) -> dict:
    return {
        "folderId": FOLDER_ID,
        "analyze_specs": [{
            "content": image_content,
            "features": [{"type": "FACE_DETECTION"}]
        }]
    }


def detect_faces(image: bytes, access_token: str, token_type: str) -> list:
    auth_headers = {
        'Content-Type': 'application/json',
        'Authorization': f'{token_type} {access_token}',
    }
    encoded_image = base64.b64encode(image).decode('UTF-8')
    request_body = create_vision_request_body(encoded_image)

    try:
        response = requests.post(VISION_API_URL, json=request_body, headers=auth_headers)
        response.raise_for_status()
        faces_data = response.json()['results'][0]['results'][0]['faceDetection']['faces']
        return [face['boundingBox']['vertices'] for face in faces_data]
    except (KeyError, requests.RequestException) as e:
        print(f'Error during face detection: {e}')
        return []


def fetch_image_from_bucket(bucket_name: str, image_key: str) -> bytes:
    s3_client = boto3.client('s3', endpoint_url=YANDEX_STORAGE_ENDPOINT_URL)
    image_response = s3_client.get_object(Bucket=bucket_name, Key=image_key)
    return image_response['Body'].read()


def create_sqs_task(image_key: str, face_coordinates: dict) -> dict:
    return {'img_key': image_key, 'coordinates': face_coordinates}


def send_tasks_to_queue(image_key: str, face_coordinates: list):
    sqs_client = boto3.client('sqs', endpoint_url=YANDEX_QUEUE_ENDPOINT_URL, region_name=AWS_REGION)
    tasks = [create_sqs_task(image_key, coords) for coords in face_coordinates]
    for task in tasks:
        sqs_client.send_message(QueueUrl=QUEUE_URL, MessageBody=json.dumps(task))


def handler(event, context) -> dict:
    bucket = event['messages'][0]['details']['bucket_id']
    image_key = event['messages'][0]['details']['object_id']
    access_token = context.token["access_token"]
    token_type = context.token["token_type"]

    image = fetch_image_from_bucket(bucket, image_key)
    face_coords = detect_faces(image, access_token, token_type)
    send_tasks_to_queue(image_key, face_coords)

    return {'statusCode': 200, 'body': 'Face detection and task dispatching completed.'}
