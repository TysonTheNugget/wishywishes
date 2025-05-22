import requests
import json
import time
import os
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app, resources={r"/check_holder_rank": {"origins": "*"}})

# --- Configuration ---
API_KEY = "1423e3815899d351c41529064e5b9a52"
ETCHING_NAME = "WISHYWASHYMACHINE"
HIRO_API_HOLDERS = "https://api.hiro.so/runes/v1/etchings/{}/holders"
HIRO_API_ETCHING = "https://api.hiro.so/runes/v1/etchings/{}"
HEADERS = {"x-api-key": API_KEY}
RATE_LIMIT_DELAY = 1
RATE_LIMIT_WAIT = 60
MAX_RETRIES = 3
LIMIT = 60
REQUEST_TIMEOUT = 10

# Save data to safe location on Render free tier
TEMP_DIR = "/tmp"
PROGRESS_FILE = os.path.join(TEMP_DIR, "progress.json")
OUTPUT_FILE = os.path.join(TEMP_DIR, "output.json")
RAW_PAGES_FILE = os.path.join(TEMP_DIR, "raw_pages.json")
NON_ZERO_FILE = os.path.join(TEMP_DIR, "non_zero_holders.json")

BIN_ID = "682f6c2f8561e97a5019f8ad"
JSONBIN_API_KEY = "$2a$10$Vx6nKwI8iapi.qt.PZBwxOg1/efwKsqCAbty90zUYefK5nnIpdFWK"

def log(msg):
    print(f"[LOG] {msg}")

def fetch_rune_metadata():
    url = HIRO_API_ETCHING.format(ETCHING_NAME)
    try:
        log(f"Fetching rune metadata from {url}")
        response = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        log(f"Error fetching rune metadata: {e}")
        return {"error": str(e)}

def fetch_page(offset, limit):
    url = HIRO_API_HOLDERS.format(ETCHING_NAME)
    params = {"offset": offset, "limit": limit}
    for attempt in range(MAX_RETRIES):
        try:
            log(f"Fetching page at offset {offset} (attempt {attempt + 1})")
            response = requests.get(url, headers=HEADERS, params=params, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            if response.status_code == 429:
                log("Rate limit hit. Sleeping...")
                time.sleep(RATE_LIMIT_WAIT)
            elif attempt == MAX_RETRIES - 1:
                return {"error": str(e)}
            time.sleep(5)
        except requests.exceptions.RequestException as e:
            log(f"Request failed: {e}")
            if attempt == MAX_RETRIES - 1:
                return {"error": str(e)}
            time.sleep(5)
    return {"error": "Max retries exceeded"}

def find_last_non_zero_page(total, limit):
    if total == 0:
        return 0
    left = 0
    right = total - 1
    last_non_zero_offset = 0
    while left <= right:
        mid = (left + right) // 2
        mid = (mid // limit) * limit
        log(f"Binary search checking offset {mid}")
        data = fetch_page(mid, limit)
        if "error" in data:
            return {"error": data["error"]}
        results = data.get("results", [])
        page_non_zero = sum(1 for h in results if int(h.get("balance", 0)) > 0)
        if page_non_zero > 0:
            last_non_zero_offset = mid
            left = mid + limit
        else:
            right = mid - limit
        time.sleep(RATE_LIMIT_DELAY)
    return last_non_zero_offset

def upload_to_jsonbin(data, bin_id, api_key):
    url = f"https://api.jsonbin.io/v3/b/{bin_id}"
    headers = {
        "Content-Type": "application/json",
        "X-Master-Key": api_key,
        "X-Bin-Versioning": "false"
    }
    try:
        log("Uploading to JSONBin...")
        response = requests.put(url, headers=headers, json=data, timeout=10)
        if response.status_code == 200:
            log("✅ JSON uploaded to JSONBin successfully.")
        else:
            log(f"❌ JSONBin upload failed: {response.text}")
    except Exception as e:
        log(f"Exception during upload: {e}")

def get_all_holders():
    log("Starting get_all_holders process")
    holders = []
    offset = 0
    non_zero_count = 0
    raw_debug_pages = []
    limit = LIMIT
    rune_metadata = fetch_rune_metadata()
    if "error" in rune_metadata:
        return rune_metadata
    data = fetch_page(0, limit)
    if "error" in data:
        return data
    total = data.get("total", 0)
    if total == 0:
        return {"error": "No holders found"}
    result = find_last_non_zero_page(total, limit)
    if isinstance(result, dict) and "error" in result:
        return result
    last_non_zero_offset = result
    while offset <= last_non_zero_offset:
        data = fetch_page(offset, limit)
        if "error" in data:
            return data
        results = data.get("results", [])
        if not results:
            break
        raw_debug_pages.append(data)
        holders.extend(results)
        page_non_zero = sum(1 for h in results if int(h.get("balance", 0)) > 0)
        non_zero_count += page_non_zero
        if page_non_zero == 0:
            break
        offset += limit
        time.sleep(RATE_LIMIT_DELAY)
    non_zero_holders = [h for h in holders if int(h.get("balance", 0)) > 0]
    with open(NON_ZERO_FILE, "w") as f:
        json.dump(non_zero_holders, f, indent=2)
    upload_to_jsonbin(non_zero_holders, BIN_ID, JSONBIN_API_KEY)
    log(f"✅ Finished fetching {len(non_zero_holders)} non-zero holders")
    return {"holders": holders, "non_zero_holders": non_zero_holders, "total": total}

@app.route("/check_holder_rank", methods=["POST"])
def check_holder_rank():
    data = request.get_json()
    user_address = data.get("address", "").strip()
    if not user_address:
        return jsonify({"error": "Please provide a BTC address"}), 400
    try:
        with open(NON_ZERO_FILE, "r") as f:
            non_zero_holders = json.load(f)
    except Exception as e:
        log(f"Error loading file: {e}")
        return jsonify({"error": "Could not load ranking data."}), 500
    for index, holder in enumerate(non_zero_holders, start=1):
        if holder.get("address") == user_address:
            balance = int(holder.get("balance", 0))
            return jsonify({
                "rank": index,
                "balance": balance,
                "non_zero_holders": len(non_zero_holders)
            })
    return jsonify({"error": "Critical error code 404: No wishy found"}), 404

@app.route("/update_holders", methods=["GET"])
def update_holders():
    try:
        log("🚀 update_holders route hit")
        result = get_all_holders()
        if "error" in result:
            log("Error in update_holders:")
            log(result["error"])
            return jsonify({"status": "error", "message": result["error"]}), 500
        return jsonify({"status": "ok", "message": "Holders updated and pushed to JSONBin."})
    except Exception as e:
        log(f"🔥 Exception in update_holders: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)