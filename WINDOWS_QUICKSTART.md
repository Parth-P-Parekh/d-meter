# Windows Quick Start: DIY Board Inference

Run every command below in Windows PowerShell on the Admin PC, from `C:\Users\Admin\Desktop\custom_infer`.

## Test one image

```powershell
python tools\run_diy_image.py --image "C:\path\to\your\image.png" --frame-id demo-001
```

The command writes these local Windows files:

```text
results\diy\demo-001.json
results\diy\demo-001.png
```

Open the overlay in the default Windows image viewer:

```powershell
Start-Process .\results\diy\demo-001.png
```

## Check board status

```powershell
python -c "from urllib.request import urlopen; print(urlopen('http://192.168.1.20:5003/health', timeout=10).read().decode())"
```

## Deploy an edited DIY service

After editing `diy_runtime\diy_inference_service.py`, copy only the two board runtime files:

```powershell
python tools\deploy_diy_runtime.py
```

## Restart the DIY service from Windows

Only run this after deployment. These are Windows SSH commands; the quoted text runs on the board.

```powershell
ssh sima@192.168.1.20 "ps -eo pid,args | grep '/home/sima/diy-inference-demo/diy_inference_service.py' | grep -v grep"
```

Copy the displayed PID, then substitute it below:

```powershell
ssh sima@192.168.1.20 "kill <DIY-PID>; nohup /home/sima/diy-inference-demo/start_diy_inference_service.sh --host 0.0.0.0 --port 5003 </dev/null >/tmp/diy-inference-demo.log 2>&1 &"
```

Wait two seconds, then use the **Check board status** command above.

Do not stop port 5001 or the `PowerBoard_simaaisrc.py` process.
