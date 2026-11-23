import boto3
from facture.config import (
    S3_ENDPOINT_URL, S3_ACCESS_KEY_ID, S3_SECRET_ACCESS_KEY,
    S3_REGION, S3_BUCKET, LANDING_DIR,
)


def get_s3_client():
    return boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT_URL,
        aws_access_key_id=S3_ACCESS_KEY_ID,
        aws_secret_access_key=S3_SECRET_ACCESS_KEY,
        region_name=S3_REGION,
    )


def fetch():
    s3 = get_s3_client()
    resp = s3.list_objects_v2(Bucket=S3_BUCKET)
    objects = resp.get("Contents", [])

    remote_pdfs = [
        obj["Key"] for obj in objects
        if obj["Key"].endswith(".pdf") and "/" not in obj["Key"]
    ]

    downloaded, skipped = 0, 0
    for key in remote_pdfs:
        dest = LANDING_DIR / key
        if dest.exists():
            skipped += 1
            continue
        s3.download_file(S3_BUCKET, key, str(dest))
        downloaded += 1

    print(f"Fetch terminé : {downloaded} téléchargés, {skipped} déjà présents.")
    return downloaded
