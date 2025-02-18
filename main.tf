# Configuration for Terraform with required providers and versions
terraform {
  required_providers {
    yandex = {
      source = "yandex-cloud/yandex" # Yandex Cloud Provider
    }
  }
  required_version = ">= 0.13" # Minimum required Terraform version
}

# Yandex Cloud provider setup
provider "yandex" {
  service_account_key_file = "C:\\Users\\znuri\\.yc-keys\\key.json"           # Service account key file
  cloud_id                 = var.yandex_cloud_id  # Cloud ID
  folder_id                = var.yandex_folder_id # Folder ID
  zone                     = "ru-central1-a"      # Availability zone
}

locals {
  service_account_id = jsondecode(file("C:\\Users\\znuri\\.yc-keys\\key.json")).service_account_id # Extracting service account ID from key file
}

resource "yandex_iam_service_account" "sa" {
  name        = "${var.yandex_folder_id}-editor"
  description = "Service account to manage Object Storage"
}

resource "yandex_resourcemanager_folder_iam_member" "editor_role" {
  folder_id = var.yandex_folder_id
  role      = "editor"
  member    = "serviceAccount:${yandex_iam_service_account.sa.id}"
}

resource "yandex_resourcemanager_folder_iam_member" "queue_role" {
  folder_id = var.yandex_folder_id
  role      = "ymq.admin"
  member    = "serviceAccount:${yandex_iam_service_account.sa.id}"
}

resource "yandex_resourcemanager_folder_iam_member" "invoker_iam" {
  folder_id = var.yandex_folder_id
  role      = "serverless.functions.invoker"
  member    = "serviceAccount:${yandex_iam_service_account.sa.id}"
}

resource "yandex_iam_service_account_static_access_key" "sa-static-key" {
  service_account_id = yandex_iam_service_account.sa.id
  description        = "Static access key for object storage"
}



# Static access key creation for the service account
#resource "yandex_iam_service_account_static_access_key" "sa-static-key" {
#  service_account_id = local.service_account_id
#}

# Bucket creation for storing photos
resource "yandex_storage_bucket" "photos" {
  access_key = yandex_iam_service_account_static_access_key.sa-static-key.access_key
  secret_key = yandex_iam_service_account_static_access_key.sa-static-key.secret_key
  bucket     = "${var.user}-photos"
  max_size   = 1048576 # Maximum bucket size in bytes (1 MB)
  anonymous_access_flags {
    read = false
    list = false
  }
}

# Bucket creation for storing processed faces
resource "yandex_storage_bucket" "faces" {
  # Configuration similar to "photos" bucket
  access_key = yandex_iam_service_account_static_access_key.sa-static-key.access_key
  secret_key = yandex_iam_service_account_static_access_key.sa-static-key.secret_key
  bucket     = "${var.user}-faces"
  max_size   = 1048576
  anonymous_access_flags {
    read = false
    list = false
  }
}

# Task queue creation
resource "yandex_message_queue" "queue-task" {
  access_key                 = yandex_iam_service_account_static_access_key.sa-static-key.access_key
  secret_key                 = yandex_iam_service_account_static_access_key.sa-static-key.secret_key
  name                       = "${var.user}-task"
  visibility_timeout_seconds = 30
  receive_wait_time_seconds  = 20
  message_retention_seconds  = 86400 # Message retention time in queue (1 day)
}

# Zipping the face detection function source code
data "archive_file" "zip-detection-face" {
  type        = "zip"
  output_path = "face_detection.zip"
  source_dir  = "./face_detection"
}

# Face detection function creation
resource "yandex_function" "face-detection" {
  name               = "${var.user}-face-detection"
  description        = "Function for face detection"
  user_hash          = "any_user_defined_string"
  runtime            = "python311"
  entrypoint         = "index.handler"
  memory             = "128" # Memory allocation in megabytes
  execution_timeout  = "10"  # Execution timeout in seconds
  service_account_id = yandex_iam_service_account.sa.id
  tags               = ["my_tag"]
  content {
    zip_filename = "face_detection.zip"
  }
  environment = {
    AWS_ACCESS_KEY_ID     = yandex_iam_service_account_static_access_key.sa-static-key.access_key
    AWS_SECRET_ACCESS_KEY = yandex_iam_service_account_static_access_key.sa-static-key.secret_key
    FOLDER_ID             = var.yandex_folder_id
    QUEUE_URL             = yandex_message_queue.queue-task.id
  }
}

# Trigger creation for the face detection function
resource "yandex_function_trigger" "photo-trigger" {
  name        = "${var.user}-photo"
  description = "Trigger for the face detection function"
  object_storage {
    batch_cutoff = 5 # Trigger activation interval
    bucket_id    = yandex_storage_bucket.photos.id
    create       = true
  }
  function {
    id                 = yandex_function.face-detection.id
    service_account_id = yandex_iam_service_account.sa.id
  }
}

# Serverless YDB database creation
resource "yandex_ydb_database_serverless" "ydb" {
  name        = "${var.user}-db-photo-face"
  location_id = "ru-central1"

  serverless_database {
    storage_size_limit = 5
  }
}

# YDB table creation
resource "yandex_ydb_table" "ydb-table" {
  path              = "faces"
  connection_string = yandex_ydb_database_serverless.ydb.ydb_full_endpoint

  column {
    name = "face_key"
    type = "String"
  }

  column {
    name = "face_name"
    type = "String"
  }

  column {
    name = "original_key"
    type = "String"
  }

  primary_key = ["face_key"]
}

# Zipping the face cut function source code
data "archive_file" "zip-face-cut" {
  type        = "zip"
  output_path = "face_cut.zip"
  source_dir  = "./face_cut"
}

# Face cut function creation
resource "yandex_function" "face-cut" {
  name               = "${var.user}-face-cut"
  description        = "Function to create photo from coordinates"
  user_hash          = "any_user_defined_string"
  runtime            = "python311"
  entrypoint         = "index.handler"
  memory             = "128"
  execution_timeout  = "10"
  service_account_id = yandex_iam_service_account.sa.id
  tags               = ["my_tag"]
  content {
    zip_filename = "face_cut.zip"
  }
  environment = {
    AWS_ACCESS_KEY_ID     = yandex_iam_service_account_static_access_key.sa-static-key.access_key
    AWS_SECRET_ACCESS_KEY = yandex_iam_service_account_static_access_key.sa-static-key.secret_key
    FROM_BUCKET_NAME      = "${var.user}-photos"
    TO_BUCKET_NAME        = "${var.user}-faces"
    YDB_DATABASE          = yandex_ydb_database_serverless.ydb.database_path
    YDB_ENDPOINT          = yandex_ydb_database_serverless.ydb.ydb_api_endpoint
  }
}

# Trigger creation for the queue processing function
resource "yandex_function_trigger" "task_trigger" {
  name        = "${var.user}-task"
  description = "Trigger for unloading the queue"
  message_queue {
    queue_id           = yandex_message_queue.queue-task.arn
    service_account_id = yandex_iam_service_account.sa.id
    batch_size         = "1"
    batch_cutoff       = "0"
  }
  function {
    id                 = yandex_function.face-cut.id
    service_account_id = yandex_iam_service_account.sa.id
  }
}

# API Gateway creation
resource "yandex_api_gateway" "gateway" {
  name = "${var.user}-apigw"
  spec = <<-EOT
openapi: 3.0.0
info:
  title: Sample API
  version: 1.0.0
paths:
  /:
    get:
      parameters:
        - name: face
          in: query
          required: true
          schema:
            type: string
      x-yc-apigateway-integration:
        type: object_storage
        bucket: ${yandex_storage_bucket.faces.id}
        object: '{face}'
        error_object: error.html
        service_account_id: ${yandex_iam_service_account.sa.id}
EOT
}

# Zipping the Telegram bot function source code
data "archive_file" "zip-tg-boott" {
  type        = "zip"
  output_path = "tg_boot.zip"
  source_dir  = "./tg_boot"
}

# Telegram bot function creation
resource "yandex_function" "boot" {
  name               = "${var.user}-boot"
  description        = "Handler function for Telegram bot"
  user_hash          = "any_user_defined_string"
  runtime            = "python311"
  entrypoint         = "index.handler"
  memory             = "128"
  execution_timeout  = "10"
  service_account_id = yandex_iam_service_account.sa.id
  tags               = ["my_tag"]
  content {
    zip_filename = "tg_boot.zip"
  }
  environment = {
    TELEGRAM_BOT_TOKEN    = var.tgkey
    AWS_ACCESS_KEY_ID     = yandex_iam_service_account_static_access_key.sa-static-key.access_key
    AWS_SECRET_ACCESS_KEY = yandex_iam_service_account_static_access_key.sa-static-key.secret_key
    YDB_DATABASE          = yandex_ydb_database_serverless.ydb.database_path
    YDB_ENDPOINT          = yandex_ydb_database_serverless.ydb.ydb_api_endpoint
    API_GATEWAY           = yandex_api_gateway.gateway.domain
    BUCKET_NAME           = "${var.user}-photos"
  }
}

# IAM binding for the Telegram bot function
resource "yandex_function_iam_binding" "boot--iam" {
  function_id = yandex_function.boot.id
  role        = "functions.functionInvoker"
  members = [
    "system:allUsers",
  ]
}

resource "null_resource" "curl" {
  provisioner "local-exec" {
    command = "curl --insecure -X POST https://api.telegram.org/bot${var.tgkey}/setWebhook?url=https://functions.yandexcloud.net/${yandex_function.boot.id}"
  }

  triggers = {
    on_version_change = var.tgkey
  }

  provisioner "local-exec" {
    when    = destroy
    command = "curl --insecure -X POST https://api.telegram.org/bot${self.triggers.on_version_change}/deleteWebhook"
  }
}

# Setting up the webhook for the Telegram bot
#data "http" "webhook" {
#  url = "https://api.telegram.org/bot${var.tgkey}/setWebhook?url=https://functions.yandexcloud.net/${yandex_function.boot.id}"
#}