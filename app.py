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

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# In-memory cache for task status
task_status = {"status": "idle", "result": None}

# --- Configuration ---
HIRO_API_KEY = "1423e3815899d351c41529064e5b9a52"
JSONBIN_API_KEY = "$2a$10$CCX5llkEdRdUdh19eH5OaOsquU8QArgAJZWERm/tYJKjXhoMFl5MG"
JSONBIN_BIN_ID_1 = "682fa4fc8561e97a501a18c6"  # First chunk
JSONBIN_BIN_ID_2 = "682fb6288960c979a59fbca6 "  # Second chunk, replace with new bin ID
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
CHUNK_SIZE = 780  # ~780 holders per chunk (~47-75KB)

def fetch_rune_metadata():
    """Fetch metadata for the rune."""
    url = HIRO_API_ETCHING.format(ETCHING_NAME)
    try:
        logger.info(f"Fetching rune metadata from {url}")
        response = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        data = response.json()
        logger.info(f"Rune Metadata: {json.dumps(data, indent=2)}")
        return {"status": "success", "data": data}
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to fetch rune metadata: {e}")
        return {"status": "error", "message": str(e)}

def upload_to_jsonbin(data, bin_id):
    """Upload JSON data to a specific JSONBin bin."""
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
        logger.info(f"✅ JSON uploaded to JSONBin bin {bin_id} successfully")
        return {"status": "success", "message": f"Data uploaded to bin {bin_id}"}
    except requests.exceptions.RequestException as e:
        error_message = f"❌ Failed to upload to JSONBin bin {bin_id}: {e}\nResponse: {response.text if 'response' in locals() else 'No response'}"
        logger.error(error_message)
        return {"status": "error", "message": error_message}

def fetch_page(offset, limit):
    """Fetch a single page of holders."""
    url = HIRO_API_HOLDERS.format(ETCHING_NAME)
    params = {"offset": offset, "limit": limit}
    for attempt in range(MAX_RETRIES):
        try:
            logger.info(f"Fetching page at offset {offset}")
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
                logger.info("No more results returned")
                break
            holders.extend(results)
            page_non_zero = sum(1 for holder in results if int(holder.get("balance", 0)) > 0)
            non_zero_count += page_non_zero
            logger.info(f"Fetched {len(results)} holders in this page. Total so far: {len(holders)}")
            logger.info(f"Non-zero balance holders in this page: {page_non_zero}. Total non-zero: {non_zero_count}")
            if page_non_zero == 0:
                logger.info("Encountered a full page of zero-balance holders. Stopping fetch.")
                break
            offset += LIMIT
            if offset >= MAX_HOLDERS:
                logger.info("Reached MAX_HOLDERS limit")
                break
            time.sleep(RATE_LIMIT_DELAY)

        # Filter non-zero holders
        non_zero_holders = [holder for holder in holders if int(holder.get("balance", 0)) > 0]
        logger.info(f"✅ Total holders fetched: {len(holders)}")
        logger.info(f"Total non-zero holders: {len(non_zero_holders)}")

        # Split into chunks
        chunk_1 = non_zero_holders[:CHUNK_SIZE]
        chunk_2 = non_zero_holders[CHUNK_SIZE:]
        upload_results = []

        # Upload first chunk
        if chunk_1:
            result_1 = upload_to_jsonbin(chunk_1, JSONBIN_BIN_ID_1)
            upload_results.append({"bin_id": JSONBIN_BIN_ID_1, "result": result_1})
            if result_1["status"] == "error":
                task_status = {"status": "error", "result": upload_results}
                return
        else:
            upload_results.append({"bin_id": JSONBIN_BIN_ID_1, "result": {"status": "skipped", "message": "No data for first chunk"}})

        # Upload second chunk
        if chunk_2:
            result_2 = upload_to_jsonbin(chunk_2, JSONBIN_BIN_ID_2)
            upload_results.append({"bin_id": JSONBIN_BIN_ID_2, "result": result_2})
            if result_2["status"] == "error":
                task_status = {"status": "error", "result": upload_results}
                return
        else:
            upload_results.append({"bin_id": JSONBIN_BIN_ID_2, "result": {"status": "skipped", "message": "No data for second chunk"}})

        task_status = {
            "status": "success",
            "result": {
                "holders_count": len(holders),
                "non_zero_holders_count": len(non_zero_holders),
                "chunk_1_count": len(chunk_1),
                "chunk_2_count": len(chunk_2),
                "upload_results": upload_results
            }
        }
    except Exception as e:
        logger.error(f"Unexpected error in fetch_holders_task: {e}", exc_info=True)
        task_status = {"status": "error", "result": {"message": f"Unexpected error: {e}"}}

@app.route("/update_holders", methods=["GET"])
def update_holders():
    """Start holder fetch in background."""
    logger.info("Received request to /update_holders")
    if task_status["status"] == "running":
        return jsonify({"status": "running", "message": "Fetch already in progress"})
    Thread(target=fetch_holders_task).start()
    return jsonify({"status": "started", "message": "Fetch started in background"})

@app.route("/status", methods=["GET"])
def status():
    """Check the status of the fetch task."""
    logger.info("Received request to /status")
    return jsonify(task_status)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))