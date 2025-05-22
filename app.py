import requests
import json
import time
import os
from flask import Flask, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# --- Configuration ---
HIRO_API_KEY = "1423e3815899d351c41529064e5b9a52"
JSONBIN_API_KEY = "$2a$10$HmW4pt5hlCQopoMmmE7.pOvVwmsm4wKTpJqVRUitVUkYf4b829ye6"
JSONBIN_BIN_ID = "682fa4fc8561e97a501a18c6"
ETCHING_NAME = "WISHYWASHYMACHINE"
HIRO_API_HOLDERS = "https://api.hiro.so/runes/v1/etchings/{}/holders"
HIRO_API_ETCHING = "https://api.hiro.so/runes/v1/etchings/{}"
HEADERS = {"x-api-key": HIRO_API_KEY}
RATE_LIMIT_DELAY = 3  # Faster but safe for Hiro API
RATE_LIMIT_WAIT = 60
MAX_RETRIES = 3
LIMIT = 60
REQUEST_TIMEOUT = 10
MAX_HOLDERS = 2000  # Cap at 2000 holders

def fetch_rune_metadata():
    """Fetch metadata for the rune to verify its details."""
    url = HIRO_API_ETCHING.format(ETCHING_NAME)
    try:
        print(f"Fetching rune metadata from {url}...")
        response = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        data = response.json()
        print(f"Rune Metadata: {json.dumps(data, indent=2)}")
        return {"status": "success", "data": data}
    except requests.exceptions.RequestException as e:
        print(f"Failed to fetch rune metadata: {e}")
        return {"status": "error", "message": str(e)}

def upload_to_jsonbin(data):
    """Upload non-zero holders to JSONBin."""
    url = f"https://api.jsonbin.io/v3/b/{JSONBIN_BIN_ID}"
    headers = {
        "Content-Type": "application/json",
        "X-Master-Key": JSONBIN_API_KEY,
        "X-Bin-Versioning": "false"
    }
    try:
        print(f"Uploading {len(data)} non-zero holders to JSONBin bin {JSONBIN_BIN_ID}...")
        response = requests.put(url, headers=headers, json=data, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        print("✅ JSON uploaded to JSONBin successfully.")
        return {"status": "success", "message": "Data uploaded to JSONBin"}
    except requests.exceptions.RequestException as e:
        error_message = f"❌ Failed to upload to JSONBin: {e}\nResponse: {response.text if 'response' in locals() else 'No response'}"
        print(error_message)
        return {"status": "error", "message": error_message}

def fetch_page(offset, limit):
    """Fetch a single page of holders."""
    url = HIRO_API_HOLDERS.format(ETCHING_NAME)
    params = {"offset": offset, "limit": limit}
    for attempt in range(MAX_RETRIES):
        try:
            response = requests.get(url, headers=HEADERS, params=params, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            return {"status": "success", "data": response.json()}
        except requests.exceptions.HTTPError as e:
            print(f"HTTP error (attempt {attempt + 1}/{MAX_RETRIES}): {e}")
            if response.status_code == 429:
                print(f"Rate limit hit, waiting {RATE_LIMIT_WAIT} seconds...")
                time.sleep(RATE_LIMIT_WAIT)
            elif attempt == MAX_RETRIES - 1:
                return {"status": "error", "message": str(e)}
            time.sleep(5)
        except requests.exceptions.RequestException as e:
            print(f"API request failed (attempt {attempt + 1}/{MAX_RETRIES}): {e}")
            if attempt == MAX_RETRIES - 1:
                return {"status": "error", "message": str(e)}
            time.sleep(5)

def find_last_non_zero_page(total, limit, max_holders):
    """Binary search to find the last page with non-zero holders within max_holders."""
    effective_total = min(total, max_holders)
    if effective_total == 0:
        return 0
    left = 0
    right = effective_total - 1
    last_non_zero_offset = 0
    while left <= right:
        mid = (left + right) // 2
        mid = (mid // limit) * limit  # Align to page boundary
        print(f"Checking offset {mid} for non-zero holders...")
        page_result = fetch_page(mid, limit)
        if page_result["status"] == "error":
            return {"status": "error", "message": page_result["message"]}
        results = page_result["data"].get("results", [])
        page_non_zero = sum(1 for holder in results if int(holder.get("balance", 0)) > 0)
        if page_non_zero > 0:
            last_non_zero_offset = mid
            left = mid + limit
        else:
            right = mid - limit
        time.sleep(RATE_LIMIT_DELAY)
    return last_non_zero_offset

def get_all_holders():
    """Fetch up to 2000 non-zero holders and upload to JSONBin."""
    holders = []
    offset = 0
    total = None
    non_zero_count = 0

    # Fetch rune metadata
    metadata_result = fetch_rune_metadata()
    if metadata_result["status"] == "error":
        return {"status": "error", "message": metadata_result["message"]}

    # Get total holders
    page_result = fetch_page(0, LIMIT)
    if page_result["status"] == "error":
        return {"status": "error", "message": page_result["message"]}
    total = page_result["data"].get("total", 0)
    print(f"Total holders reported by API: {total}")
    if total == 0:
        return {"status": "error", "message": "No holders found for this rune"}

    # Find last page with non-zero holders within MAX_HOLDERS
    last_non_zero_offset_result = find_last_non_zero_page(total, LIMIT, MAX_HOLDERS)
    if isinstance(last_non_zero_offset_result, dict) and last_non_zero_offset_result["status"] == "error":
        return last_non_zero_offset_result
    last_non_zero_offset = last_non_zero_offset_result

    # Fetch holders up to last non-zero page or MAX_HOLDERS
    while offset <= min(last_non_zero_offset, MAX_HOLDERS - LIMIT):
        print(f"Fetching offset {offset} (page {offset // LIMIT + 1})...")
        page_result = fetch_page(offset, LIMIT)
        if page_result["status"] == "error":
            return {"status": "error", "message": page_result["message"]}
        results = page_result["data"].get("results", [])
        if not results:
            print("No more results returned")
            break
        holders.extend(results)
        page_non_zero = sum(1 for holder in results if int(holder.get("balance", 0)) > 0)
        non_zero_count += page_non_zero
        print(f"Fetched {len(results)} holders in this page. Total so far: {len(holders)}")
        print(f"Non-zero balance holders in this page: {page_non_zero}. Total non-zero: {non_zero_count}")
        if page_non_zero == 0:
            print("Encountered a full page of zero-balance holders. Stopping fetch.")
            break
        offset += LIMIT
        time.sleep(RATE_LIMIT_DELAY)

    # Filter non-zero holders
    non_zero_holders = [holder for holder in holders if int(holder.get("balance", 0)) > 0]
    print(f"✅ Total holders fetched: {len(holders)}")
    print(f"Total non-zero holders: {len(non_zero_holders)}")

    # Upload to JSONBin
    upload_result = upload_to_jsonbin(non_zero_holders)
    if upload_result["status"] == "error":
        return {"status": "error", "message": upload_result["message"]}

    return {
        "status": "success",
        "holders_count": len(holders),
        "non_zero_holders_count": len(non_zero_holders),
        "upload_result": upload_result
    }

@app.route("/update_holders", methods=["GET"])
def update_holders():
    """Endpoint to fetch holders and upload to JSONBin."""
    print("Received request to /update_holders")
    result = get_all_holders()
    return jsonify(result)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))