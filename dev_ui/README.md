# Temporary DEV UI

This directory contains a development-only, read-only Streamlit inspector for the
normalized files in `data/processed/`.

From the project root, install dependencies and run:

```powershell
python -m pip install -r requirements.txt
streamlit run dev_ui/app.py
```

The UI does not edit CSV files or the raw workbook, run preprocessing, save manual
changes, or perform planning. It is not part of the final product. The entire
`dev_ui/` directory can be removed before final submission without breaking the
preprocessing or production modules.
