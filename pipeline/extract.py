"""Extracts necessary information from S3 Bucket"""

from collections import defaultdict
import os
import logging
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from boto3 import client
from botocore.config import Config
from botocore.exceptions import ClientError
from numpy import logical_and
import pandas as pd
from dotenv import load_dotenv
from pytrends.request import TrendReq
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

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
        max_pool_connections=100
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


def extract_bluesky_files(s3: client) -> list[str]:
    """Accesses files from S3 and returns a list of texts with the topic present"""
    continuation_token = None
    file_names = []

    bucket_parameters = {'Bucket': os.environ.get("S3_BUCKET_NAME")}
    if not bucket_parameters.get('Bucket'):
        raise KeyError("Missing environment variable: S3_BUCKET_NAME")

    while True:
        if continuation_token:
            bucket_parameters['ContinuationToken'] = continuation_token

        response = s3.list_objects_v2(**bucket_parameters)
        contents = response.get("Contents")

        if not contents:
            break

        for file in contents:
            if file['Key'].endswith(".json"):
                file_names.append(file['Key'])

        continuation_token = response.get('NextContinuationToken')
        if not continuation_token:
            break

    return file_names


def format_time(combined_time: str) -> str:
    """Formats a string into a readable time"""
    hours = combined_time[:2]
    minutes = combined_time[2:4]
    seconds = combined_time[4:]

    return f"{hours}:{minutes}:{seconds}"


def fetch_metadata(s3: client, file_name: str, topic: list[str]) -> dict:
    """Checks files from S3 for keywords and returns relevant data if keyword is found"""
    try:
        file_obj = s3.get_object(Bucket=os.environ.get(
            "S3_BUCKET_NAME"), Key=file_name)
        file_content = file_obj['Body'].read().decode('utf-8')
        data = json.loads(file_content)

        stored_keywords = data.get('Keywords', {})
        stored_sentiment = data.get('Sentiment Score', {})
        hour_folder = file_name.split('/')[-2]
        date_and_hour = "/".join(file_name.split('/')[1:3])
        time = format_time(file_name.split('/')[-1].split('.')[0][8:14])

        keyword_counts = {}
        sentiment_scores = {}

        for keyword in topic:
            if keyword in stored_keywords:
                keyword_counts[keyword] = stored_keywords.get(keyword, 0)

                sentiment_score = stored_sentiment.get('compound', 0)
                sentiment_scores[keyword] = sentiment_score
                logging.info(
                    "Keyword: '%s' found in S3 Bucket in folder %s at time %s", keyword, date_and_hour, time)

        return {
            "Hour": hour_folder,
            "Counts": keyword_counts,
            "Sentiment Score": sentiment_scores
        }

    except ClientError as e:
        if e.response['Error']['Code'] == 'NoSuchKey':
            logging.error("File not found in S3: %s", e)
            raise FileNotFoundError(
                f"File '{file_name}' not found in S3.") from e

    return None


def multi_threading_matching(s3: client, topic: list[str], file_names: list[str]) -> pd.DataFrame:
    """Uses multi-threading to extract keyword counts and sentiment scores from S3 metadata."""
    hourly_data = defaultdict(lambda: defaultdict(int))
    hourly_sentiments = defaultdict(lambda: defaultdict(list))

    with ThreadPoolExecutor(max_workers=100) as thread_pool:
        submitted_tasks = [thread_pool.submit(
            fetch_metadata, s3, file_name, topic) for file_name in file_names]

        for completed_task in as_completed(submitted_tasks):
            extracted_data = completed_task.result()
            if extracted_data["Counts"]:

                hour = extracted_data["Hour"]

                for keyword, count in extracted_data["Counts"].items():
                    hourly_data[hour][keyword] += count

                for keyword, sentiment_score in extracted_data["Sentiment Score"].items():
                    hourly_sentiments[hour][keyword].append(sentiment_score)

    hourly_rows = []
    for hour, counts in hourly_data.items():
        for keyword, count in counts.items():
            average_sentiment = (
                sum(hourly_sentiments[hour][keyword]) /
                len(hourly_sentiments[hour][keyword])
                if hourly_sentiments[hour][keyword] else 0)
            hourly_rows.append({"Hour": hour, "Keyword": keyword,
                               "Count": count, "Average Sentiment": average_sentiment})
    mentions_per_hour = pd.DataFrame(hourly_rows)

    return mentions_per_hour


def initialize_trend_request() -> TrendReq:
    """Initialize and return a TrendReq object."""
    return TrendReq()


def fetch_suggestions(pytrend: TrendReq, keyword: str) -> list[dict]:
    """Fetch and print suggestions for a given keyword."""
    return pytrend.suggestions(keyword=keyword)


def main(topic: list[str]) -> pd.DataFrame:
    """Extracts data from S3 Bucket and creates two summary DataFrames"""
    s3 = s3_connection()
    filenames = extract_bluesky_files(s3)
    hourly_statistics = multi_threading_matching(s3, topic, filenames)
    return hourly_statistics
    hourly_statistics['Related Terms'] = ""

    pytrend = initialize_trend_request()
    for keyword in topic:
        hourly_statistics.loc[hourly_statistics['Keyword'] == keyword, 'Related Terms'] = ",".join(
            [suggestion['title']
                for suggestion in fetch_suggestions(pytrend, keyword)]
        )
    return hourly_statistics


if __name__ == "__main__":
    topics = ['happy']
    extracted_dataframe = main(topics)
    logging.info("\nExtracted Dataframe:\n%s", extracted_dataframe)
