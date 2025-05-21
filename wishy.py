# app.py
import requests
import json
import time
import os
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)  # Enable CORS to allow frontend requests

# --- Configuration ---
API_KEY = "1423e3815899d351c41529064e5b9a52"
ETCHING_NAME = "WISHYWASHYMACHINE"
HIRO_API_HOLDERS = "https://api.hiro.so/runes/v1/etchings/{}/holders"
HIRO_API_ETCHING = "https://api.hiro.so/runes/v1/etchings/{}"
HEADERS = {"x-api-key": API_KEY}
RATE_LIMIT_DELAY = 10  # Delay between requests
RATE_LIMIT_WAIT = 60  # Wait after 429 error
MAX_RETRIES = 3
PROGRESS_FILE = "progress.json"


def fetch_rune_metadata():
    """Fetch metadata for the rune to verify its details."""
    url = HIRO_API_ETCHING.format(ETCHING_NAME)
    try:
        response = requests.get(url, headers=HEADERS)
        response.raise_for_status()
        data = response.json()
        with open("rune_metadata.json", "w") as f:
            json.dump(data, f, indent=2)
        return data
    except requests.exceptions.RequestException as e:
        return {"error": f"Failed to fetch rune metadata: {str(e)}"}


def load_progress():
    """Load fetching progress from file."""
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, "r") as f:
            return json.load(f)
    return {"holders": [], "offset": 0, "total": None, "non_zero_count": 0}


def save_progress(holders, offset, total, non_zero_count):
    """Save fetching progress to file."""
    progress = {"holders": holders, "offset": offset, "total": total, "non_zero_count": non_zero_count}
    with open(PROGRESS_FILE, "w") as f:
        json.dump(progress, f, indent=2)


def get_all_holders():
    # Load progress if exists
    progress = load_progress()
    holders = progress["holders"]
    offset = progress["offset"]
    total = progress["total"]
    non_zero_count = progress["non_zero_count"]

    raw_debug_pages = []
    limit = 60

    # Fetch rune metadata for verification
    rune_metadata = fetch_rune_metadata()
    if "error" in rune_metadata:
        return rune_metadata

    while True:
        print(f"Fetching offset {offset} (page {offset // limit + 1})...")
        url = HIRO_API_HOLDERS.format(ETCHING_NAME)
        params = {"offset": offset, "limit": limit}
        
        for attempt in range(MAX_RETRIES):
            try:
                response = requests.get(url, headers=HEADERS, params=params)
                response.raise_for_status()
                break
            except requests.exceptions.HTTPError as e:
                print(f"HTTP error (attempt {attempt + 1}/{MAX_RETRIES}): {e}")
                if response.status_code == 429:
                    print(f"Rate limit hit, waiting {RATE_LIMIT_WAIT} seconds...")
                    time.sleep(RATE_LIMIT_WAIT)
                elif attempt == MAX_RETRIES - 1:
                    save_progress(holders, offset, total, non_zero_count)
                    return {"error": f"Failed after retries: {str(e)}"}
                time.sleep(5)
            except requests.exceptions.RequestException as e:
                print(f"API request failed (attempt {attempt + 1}/{MAX_RETRIES}): {e}")
                if attempt == MAX_RETRIES - 1:
                    save_progress(holders, offset, total, non_zero_count)
                    return {"error": f"Failed to fetch data: {str(e)}"}
                time.sleep(5)

        try:
            data = response.json()
        except json.JSONDecodeError:
            print(f"Failed to parse JSON response. Response content: {response.text}")
            save_progress(holders, offset, total, non_zero_count)
            return {"error": "Invalid JSON response from API"}

        if total is None:
            total = data.get("total", 0)
            print(f"Total holders reported by API: {total}")
            if total == 0:
                return {"error": "No holders found for this rune"}

        results = data.get("results", [])
        if not results:
            print("No more results returned")
            break

        raw_debug_pages.append(data)
        holders.extend(results)

        # Count non-zero balance holders in this page
        page_non_zero = sum(1 for holder in results if int(holder.get("balance", 0)) > 0)
        non_zero_count += page_non_zero

        print(f"Fetched {len(results)} holders in this page. Total so far: {len(holders)}")
        print(f"Non-zero balance holders in this page: {page_non_zero}. Total non-zero: {non_zero_count}")

        # Stop if we encounter a full page of zero-balance holders
        if page_non_zero == 0:
            print("Encountered a full page of zero-balance holders. Stopping fetch.")
            break

        offset += limit
        if offset >= total:
            break

        # Save progress after each page
        save_progress(holders, offset, total, non_zero_count)
        time.sleep(RATE_LIMIT_DELAY)

    # Save full holder list
    with open("output.json", "w") as f:
        json.dump(holders, f, indent=2)

    # Save all raw API pages for debugging
    with open("raw_pages.json", "w") as f:
        json.dump(raw_debug_pages, f, indent=2)

    # Save non-zero holders
    non_zero_holders = [holder for holder in holders if int(holder.get("balance", 0)) > 0]
    with open("non_zero_holders.json", "w") as f:
        json.dump(non_zero_holders, f, indent=2)

    print(f"✅ Total holders fetched: {len(holders)}")
    print(f"Total holders with non-zero balance: {len(non_zero_holders)}")

    return {"holders": holders, "non_zero_holders": non_zero_holders, "total": total}


@app.route("/check_holder_rank", methods=["POST"])
def check_holder_rank():
    data = request.get_json()
    user_address = data.get("address", "").strip()

    if not user_address:
        return jsonify({"error": "Please provide a BTC address"}), 400

    result = get_all_holders()
    if "error" in result:
        return jsonify({"error": result["error"]}), 500

    holders = result["holders"]
    non_zero_holders = result["non_zero_holders"]

    found = False
    for index, holder in enumerate(non_zero_holders, start=1):
        if holder.get("address") == user_address:
            balance = int(holder.get("balance", 0))
            return jsonify({
                "rank": index,
                "balance": balance,
                "non_zero_holders": len(non_zero_holders)
            })
        found = True

    return jsonify({"error": "Critical error code 404: No wishy found"}), 404


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))