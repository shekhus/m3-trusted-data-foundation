#!/bin/sh
# Portal entrypoint (D-032): a thin Streamlit client of the API at API_BASE_URL.
exec streamlit run portal/app.py --server.port "${PORT:-8501}" --server.address 0.0.0.0 \
  --server.headless true --browser.gatherUsageStats false
