# Modalix command sheet

Run each command explicitly over SSH as `sima`. These checks are intentionally conservative: if a path, listener, or process differs from the expected isolated names, stop and investigate. Never kill, overwrite, or reconfigure port 5001, port 5003, `PowerBoard_simaaisrc.py`, or the DIY service.

## Before MPK installation or service start

```bash
test "$(id -un)" = sima
test ! -e /data/simaai/applications/yolo26n_coco_simaaisrc
test ! -e /home/sima/yolo26-inference-demo || test -d /home/sima/yolo26-inference-demo
! ss -ltnp 'sport = :5004' | tail -n +2 | grep -q .
test -x /home/sima/yolo26-inference-demo/verify_guardrails.sh
/home/sima/yolo26-inference-demo/verify_guardrails.sh snapshot
```

The snapshot must show both protected listeners. From the Ubuntu Palette container, connect and deploy only the new application name:

```bash
cd /home/docker/sima-cli/yolo26_modalix/artifacts/package/yolo26n_coco_simaaisrc
mpk device connect -t sima@192.168.1.20
mpk deploy -f project.mpk
```

Back on Modalix, validate only the new installation and hashes:

```bash
test -d /data/simaai/applications/yolo26n_coco_simaaisrc
test -f /data/simaai/applications/yolo26n_coco_simaaisrc/etc/0_preproc.json
test -f /data/simaai/applications/yolo26n_coco_simaaisrc/etc/0_process_mla.json
test -f /data/simaai/applications/yolo26n_coco_simaaisrc/etc/0_postproc.json
test -f /home/sima/yolo26-inference-demo/artifact-manifest.json
python3 -c 'import cv2, flask, numpy, gi; print(cv2.__version__, flask.__version__, numpy.__version__)'
```

If any check fails, do not change the board’s system Python or other applications; record the SDK/runtime compatibility blocker.

## Start only port 5004

```bash
nohup /home/sima/yolo26-inference-demo/start_yolo26_service.sh --host 0.0.0.0 --port 5004 \
  </dev/null >/tmp/yolo26-inference-demo.log 2>&1 &
sleep 2
ss -ltnp 'sport = :5004'
curl -fsS http://127.0.0.1:5004/health
curl -fsS http://127.0.0.1:5004/contract
```

If port 5004 is already occupied, do not use a broad kill. Display the exact owner with `ss -ltnp 'sport = :5004'` and continue only when its command is `/home/sima/yolo26-inference-demo/board_service.py` and you intentionally mean to restart this experiment.

## Evaluation and protected-service comparison

From Windows, submit all 25 held-out images, compare their JSON against FP32 ONNX, and run the fixed 50-request benchmark:

```powershell
.\experiments\yolo26_modalix\.venv\Scripts\python.exe .\experiments\yolo26_modalix\tools\submit_evaluation.py
.\experiments\yolo26_modalix\.venv\Scripts\python.exe .\experiments\yolo26_modalix\tools\compare_results.py --reference .\experiments\yolo26_modalix\results\pt-onnx\onnx --candidate .\experiments\yolo26_modalix\results\board --mode int8 --output .\experiments\yolo26_modalix\results\modalix-parity.json
.\experiments\yolo26_modalix\.venv\Scripts\python.exe .\experiments\yolo26_modalix\tools\benchmark_board.py --image "C:\path\held-out.png"
```

Finally, on Modalix:

```bash
/home/sima/yolo26-inference-demo/verify_guardrails.sh compare
```

The compare must show no listener/PID or health-status changes for ports 5001 and 5003.
