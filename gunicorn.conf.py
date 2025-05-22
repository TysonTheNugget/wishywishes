# gunicorn.conf.py
timeout = 3600  # 1 hour, to handle 8540 holders
workers = 1     # Single worker to avoid memory issues on free tier