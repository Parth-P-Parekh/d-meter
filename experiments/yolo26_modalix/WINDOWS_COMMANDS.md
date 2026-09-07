# Windows command sheet

Run in PowerShell from `C:\Users\Admin\Desktop\custom_infer`. These commands only write below `experiments\yolo26_modalix` and `results\yolo26`.

## Environment, export, and tests

```powershell
python -m venv .\experiments\yolo26_modalix\.venv
.\experiments\yolo26_modalix\.venv\Scripts\python.exe -m pip install --upgrade pip
.\experiments\yolo26_modalix\.venv\Scripts\python.exe -m pip install -r .\experiments\yolo26_modalix\requirements-windows.txt
.\experiments\yolo26_modalix\.venv\Scripts\python.exe .\experiments\yolo26_modalix\windows\export_model.py
.\experiments\yolo26_modalix\.venv\Scripts\python.exe -m pytest .\experiments\yolo26_modalix\tests -q
```

Never copy or download weights on Modalix. Transfer the finalized package and runtime only after checking their manifest hashes.

## Create the disjoint corpus

The first directory needs at least 100 representative pipeline frames. The second needs at least 20 other pipeline frames. The third needs at least five recorded images that you have visually confirmed contain recognizable COCO objects.

```powershell
.\experiments\yolo26_modalix\.venv\Scripts\python.exe .\experiments\yolo26_modalix\tools\prepare_corpus.py `
  --calibration-dir "C:\path\calibration-source" `
  --pipeline-evaluation-dir "C:\path\held-out-pipeline" `
  --coco-evaluation-dir "C:\path\held-out-coco"
.\experiments\yolo26_modalix\.venv\Scripts\python.exe .\experiments\yolo26_modalix\tools\validate_corpus.py
.\experiments\yolo26_modalix\.venv\Scripts\python.exe .\experiments\yolo26_modalix\tools\validate_pt_onnx.py
```

The corpus tool deterministically selects by source SHA-256 and rejects duplicate or overlapping content.

## Local reference and board client

```powershell
.\experiments\yolo26_modalix\.venv\Scripts\python.exe .\experiments\yolo26_modalix\windows\reference_service.py
```

In another PowerShell window:

```powershell
.\experiments\yolo26_modalix\.venv\Scripts\python.exe .\experiments\yolo26_modalix\windows\run_image.py --image "C:\path\image.png" --frame-id reference-001 --base-url http://127.0.0.1:5004
```

After a valid MPK has been built and its checksum finalized, stage only the board service:

```powershell
.\experiments\yolo26_modalix\.venv\Scripts\python.exe .\experiments\yolo26_modalix\tools\finalize_manifest.py --mpk .\experiments\yolo26_modalix\artifacts\package\yolo26n_coco_simaaisrc\project.mpk
.\experiments\yolo26_modalix\.venv\Scripts\python.exe .\experiments\yolo26_modalix\tools\deploy_board_runtime.py
```

The staging tool does not deploy an MPK or start/stop any process.

