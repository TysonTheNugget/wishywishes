import requests
import json
import time
import os
import logging
from flask import Flask, jsonify
from flask_cors import CORS
from threading import Thread

app = Flask(__name__)
CORS(app)

# Configure logging to file
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('app.log'),  # Log to file
        logging.StreamHandler()  # Minimal console output
    ]
)
logger = logging.getLogger(__name__)

# In-memory cache for task status
task_status = {"status": "idle", "result": None}

# --- Configuration ---
HIRO_API_KEY = "1423e3815899d351c41529064e5b9a52"
JSONBIN_API_KEY = "$2a$10$enXFuorMjZo.BrAOmdQSiOX52uDiXOp0ibKb.pyQ7SN9g6vbhOqBi"
JSONBIN_BIN_ID_1 = "684f94a48960c979a5aa979c"
JSONBIN_BIN_ID_2 = "684f949d8960c979a5aa9798"  # Replace
JSONBIN_BIN_ID_3 = "684f948e8a456b7966aed652"  # Replace
ETCHING_NAME = "WISHYWASHYMACHINE"
HIRO_API_HOLDERS = "https://api.hiro.so/runes/v1/etchings/{}/holders"
HIRO_API_ETCHING = "https://api.hiro.so/runes/v1/etchings/{}"
HEADERS = {"x-api-key": HIRO_API_KEY}
RATE_LIMIT_DELAY = 2
RATE_LIMIT_WAIT = 60
MAX_RETRIES = 3
LIMIT = 60
REQUEST_TIMEOUT = 10
MAX_HOLDERS = 2000
CHUNK_SIZE = 520

def fetch_rune_metadata():
    """Fetch metadata for the rune."""
    url = HIRO_API_ETCHING.format(ETCHING_NAME)
    try:
        logger.info(f"Fetching rune metadata from {url}")
        response = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        data = response.json()
        logger.debug(f"Rune Metadata: {json.dumps(data, indent=2)}")  # Debug to file
        return {"status": "success", "data": data}
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to fetch rune metadata: {e}")
        return {"status": "error", "message": str(e)}

def upload_to_jsonbin(data, bin_id):
    """Upload JSON data to a JSONBin bin."""
    url = f"https://api.jsonbin.io/v3/b/{bin_id}"
    headers = {
        "Content-Type": "application/json",
        "X-Master-Key": JSONBIN_API_KEY,
        "X-Bin-Versioning": "false"
    }
    try:
        logger.info(f"Uploading {len(data)} holders to JSONBin bin {bin_id}")
        response = requests.put(url, headers=headers, json=data, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        logger.info(f"JSON uploaded to bin {bin_id}")
        return {"status": "success", "message": f"Data uploaded to bin {bin_id}"}
    except requests.exceptions.RequestException as e:
        error_message = f"Failed to upload to bin {bin_id}: {e}"
        logger.error(error_message)
        return {"status": "error", "message": error_message}

def fetch_page(offset, limit):
    """Fetch a single page of holders."""
    url = HIRO_API_HOLDERS.format(ETCHING_NAME)
    params = {"offset": offset, "limit": limit}
    for attempt in range(MAX_RETRIES):
        try:
            logger.debug(f"Fetching page at offset {offset}")  # Debug to file
            response = requests.get(url, headers=HEADERS, params=params, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            return {"status": "success", "data": response.json()}
        except requests.exceptions.HTTPError as e:
            logger.error(f"HTTP error (attempt {attempt + 1}/{MAX_RETRIES}): {e}")
            if response.status_code == 429:
                logger.info(f"Rate limit hit, waiting {RATE_LIMIT_WAIT} seconds")
                time.sleep(RATE_LIMIT_WAIT)
            elif attempt == MAX_RETRIES - 1:
                return {"status": "error", "message": str(e)}
            time.sleep(5)
        except requests.exceptions.RequestException as e:
            logger.error(f"API request failed (attempt {attempt + 1}/{MAX_RETRIES}): {e}")
            if attempt == MAX_RETRIES - 1:
                return {"status": "error", "message": str(e)}
            time.sleep(5)

def fetch_holders_task():
    """Background task to fetch and upload holders in chunks."""
    global task_status
    try:
        task_status = {"status": "running", "result": None}
        holders = []
        offset = 0
        non_zero_count = 0

        # Fetch rune metadata
        metadata_result = fetch_rune_metadata()
        if metadata_result["status"] == "error":
            task_status = {"status": "error", "result": metadata_result}
            return

        # Fetch holders
        while offset < MAX_HOLDERS:
            page_result = fetch_page(offset, LIMIT)
            if page_result["status"] == "error":
                task_status = {"status": "error", "result": page_result}
                return
            results = page_result["data"].get("results", [])
            if not results:
                logger.info("No more results")
                break
            holders.extend(results)
            page_non_zero = sum(1 for holder in results if int(holder.get("balance", 0)) > 0)
            non_zero_count += page_non_zero
            logger.debug(f"Fetched {len(results)} holders. Total: {len(holders)}, Non-zero: {non_zero_count}")
            if page_non_zero == 0:
                logger.info("Stopped at zero-balance holders")
                break
            offset += LIMIT
            if offset >= MAX_HOLDERS:
                logger.info("Reached MAX_HOLDERS")
                break
            time.sleep(RATE_LIMIT_DELAY)

        # Filter non-zero holders
        non_zero_holders = [holder for holder in holders if int(holder.get("balance", 0)) > 0]
        logger.info(f"Total holders: {len(holders)}, Non-zero: {len(non_zero_holders)}")

        # Split into three chunks
        chunk_1 = non_zero_holders[:CHUNK_SIZE]
        chunk_2 = non_zero_holders[CHUNK_SIZE:2*CHUNK_SIZE]
        chunk_3 = non_zero_holders[2*CHUNK_SIZE:]
        upload_results = []

        # Upload chunks
        for chunk, bin_id in [(chunk_1, JSONBIN_BIN_ID_1), (chunk_2, JSONBIN_BIN_ID_2), (chunk_3, JSONBIN_BIN_ID_3)]:
            if chunk:
                result = upload_to_jsonbin(chunk, bin_id)
                upload_results.append({"bin_id": bin_id, "result": result, "count": len(chunk)})
                if result["status"] == "error":
                    task_status = {"status": "error", "result": upload_results}
                    return
            else:
                upload_results.append({"bin_id": bin_id, "result": {"status": "skipped", "message": f"No data for bin {bin_id}"}, "count": 0})

        task_status = {
            "status": "success",
            "result": {
                "holders_count": len(holders),
                "non_zero_holders_count": len(non_zero_holders),
                "chunk_counts": [len(chunk_1), len(chunk_2), len(chunk_3)],
                "upload_results": upload_results
            }
        }
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        task_status = {"status": "error", "result": {"message": f"Unexpected error: {e}"}}

@app.route("/update_holders", methods=["GET"])
def update_holders():
    """Start holder fetch in background."""
    print("Starting holder fetch")  # Minimal console output
    logger.info("Received request to /update_holders")
    if task_status["status"] == "running":
        return jsonify({"status": "running", "message": "Fetch in progress"})
    Thread(target=fetch_holders_task).start()
    return jsonify({"status": "started", "message": "Fetch started"})

@app.route("/status", methods=["GET"])
def status():
    """Check the status of the fetch task."""
    logger.info("Received request to /status")
    return jsonify(task_status)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))