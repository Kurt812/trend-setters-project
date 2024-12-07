"""Potential final extract script!"""

from ast import Return
import os
import logging
import json
import pandas as pd
import pytrends
from boto3 import client
from dotenv import load_dotenv
from pytrends.request import TrendReq
from botocore.config import Config

load_dotenv(".env")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)


def s3_connection() -> client:
    """Connects to an S3 and configs S3 Connection"""
    config = Config(
        connect_timeout=5,
        read_timeout=10,
        retries={
            'max_attempts': 3,
            'mode': 'standard'
        },
        max_pool_connections=110
    )
    try:
        aws_access_key = os.environ.get("AWS_ACCESS_KEY_ID")
        aws_secret_key = os.environ.get("AWS_SECRET_ACCESS_KEY")

        if not aws_access_key or not aws_secret_key:
            logging.error("Missing required AWS credentials in .env file.")
            raise
        s3 = client(
            "s3",
            aws_access_key_id=aws_access_key,
            aws_secret_access_key=aws_secret_key,
            config=config
        )
    except ConnectionError as e:
        logging.error('An error occurred attempting to connect to S3: %s', e)
        return None
    return s3


def average_sentiment_analysis(keyword: str, file_data: dict) -> tuple:
    """Calculates the average sentiment for a keyword in a .json file"""
    total_sentiment = 0
    mentions = 0
    for text, sentiment in file_data.items():
        if keyword in text:
            total_sentiment += sentiment['Sentiment Score']['compound']
            mentions += 1
    if mentions == 0:
        return 0, 0
    return total_sentiment/mentions, mentions


def download_truck_data_files(s3, bucket, topic: list[str]) -> pd.DataFrame:
    """Downloads relevant files from S3 to a data/ folder."""
    bucket = os.environ.get("S3_BUCKET_NAME")

    pytrend = initialize_trend_request()

    response = s3.list_objects_v2(
        Bucket=bucket, Prefix="bluesky/2024-12-07/", Delimiter='/')

    if 'Contents' in response:
        sentiment_and_mention_data = []

        for obj in response['Contents']:
            key = obj['Key']

            if key.endswith('.json') and key.count('/') == "bluesky/2024-12-07/".count('/'):
                file_obj = s3.get_object(Bucket=bucket, Key=key)
                file_content = json.loads(
                    file_obj['Body'].read().decode('utf-8'))

                for keyword in topic:
                    sentiment_and_mentions = average_sentiment_analysis(
                        keyword, file_content)

                    sentiment_and_mention_data.append({
                        'Hour': key.split("/")[-1].split(".")[0],
                        'Keyword': keyword,
                        'Average Sentiment': sentiment_and_mentions[0],
                        'Total Mentions': sentiment_and_mentions[1],
                    })
        return pd.DataFrame(sentiment_and_mention_data)
    else:
        logging.info("No files found in the parent folder.")
        raise ValueError("No files found in the parent folder.")


def initialize_trend_request() -> TrendReq:
    """Initialize and return a TrendReq object."""
    return TrendReq()


def fetch_suggestions(pytrend: TrendReq, keyword: str) -> list[dict]:
    """Fetch and print suggestions for a given keyword."""
    return pytrend.suggestions(keyword=keyword)


def main(topic: list[str]):
    """Extracts data from S3 Bucket and creates two summary DataFrames"""
    s3 = s3_connection()

    bucket = os.environ.get("S3_BUCKET_NAME")

    extracted_dataframe = download_truck_data_files(s3, bucket, topic)

    pytrend = initialize_trend_request()
    for keyword in topic:
        extracted_dataframe.loc[extracted_dataframe['Keyword'] == keyword, 'Related Terms'] = ",".join(
            [suggestion['title']
                for suggestion in fetch_suggestions(pytrend, keyword)]
        )
    return extracted_dataframe


if __name__ == "__main__":
    ...
