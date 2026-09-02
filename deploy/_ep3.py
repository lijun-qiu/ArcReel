import json, urllib.request, urllib.parse, time, sys
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

pwd = next(l.split("=",1)[1].strip() for l in Path(".env").read_text(encoding="utf-8").splitlines() if l.startswith("AUTH_PASSWORD="))
pid="proj-513920e9"
base=f"http://localhost:1241/api/v1/projects/{pid}"
script=json.loads(Path(f"projects/{pid}/scripts/episode_3.json").read_text(encoding="utf-8"))
by={s["segment_id"]:s for s in script["segments"]}
log=Path("_ep3_prog.txt")

def auth():
    body = urllib.parse.urlencode({"username":"admin","password":pwd}).encode()
    tok = json.load(urllib.request.urlopen(urllib.request.Request("http://localhost:1241/api/v1/auth/token", data=body, headers={"Content-Type":"application/x-www-form-urlencoded"}, method="POST")))["access_token"]
    return tok

def wait_idle(tok, timeout=120):
    deadline=time.time()+timeout
    while time.time()<deadline:
        data=json.load(urllib.request.urlopen(urllib.request.Request(f"{base}/tasks?page_size=50", headers={"Authorization":f"Bearer {tok}"}), timeout=30))
        active=[t for t in (data.get("items") or []) if str(t.get("status","")).lower() in ("queued","running","pending")]
        if not active: return True
        time.sleep(2)
    return False

def enqueue_sb(tok, sid):
    seg=by[sid]
    payload={"prompt": seg["image_prompt"], "script_file":"episode_3.json"}
    H={"Authorization":f"Bearer {tok}","Content-Type":"application/json"}
    req=urllib.request.Request(f"{base}/generate/storyboard/{sid}", data=json.dumps(payload, ensure_ascii=False).encode(), headers=H, method="POST")
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.load(resp)

def wait_file(path, tok, sid, kind="storyboard", timeout=240):
    deadline=time.time()+timeout
    while time.time()<deadline:
        if path.exists() and path.stat().st_size>1000:
            return True, None
        data=json.load(urllib.request.urlopen(urllib.request.Request(f"{base}/tasks?page_size=40", headers={"Authorization":f"Bearer {tok}"}), timeout=30))
        for t in data.get("items") or []:
            if t.get("resource_id")==sid and t.get("task_type")==kind and str(t.get("status")).lower() in ("failed","error"):
                return False, t.get("error_message")
        time.sleep(4)
    return path.exists() and path.stat().st_size>1000, "timeout"

missing=[sid for sid in [f"E3S{i:02d}" for i in range(1,11)] if not Path(f"projects/{pid}/storyboards/scene_{sid}.png").exists()]
print("missing boards", missing)
with open(log,"a",encoding="utf-8") as f: f.write(f"\n=== {time.strftime('%H:%M:%S')} missing {missing}\n")

for sid in missing:
    for attempt in range(1,4):
        tok=auth()
        wait_idle(tok)
        try:
            r=enqueue_sb(tok, sid)
            print(f"enqueued {sid} attempt={attempt} {r.get('task_id','')[:8]}")
        except Exception as ex:
            err=ex.read().decode("utf-8","replace") if hasattr(ex,"read") else str(ex)
            print(f"enqueue_err {sid} {err[:300]}")
            time.sleep(5); continue
        ok, err = wait_file(Path(f"projects/{pid}/storyboards/scene_{sid}.png"), tok, sid, "storyboard", 300)
        if ok:
            print("OK board", sid); break
        print("retry board", sid, err[:200] if err else None)
        time.sleep(3)
    else:
        print("GIVEUP board", sid)

boards=sorted(Path(f"projects/{pid}/storyboards").glob("scene_E3S*.png"))
print("boards now", len(boards), [b.name for b in boards])

# reload script for video prompts / assets
script=json.loads(Path(f"projects/{pid}/scripts/episode_3.json").read_text(encoding="utf-8"))
changed=False
for seg in script["segments"]:
    if seg.get("needs_replan"):
        seg.pop("needs_replan", None); changed=True
    vp=seg.get("video_prompt")
    if isinstance(vp, dict) and vp.get("dialogue"):
        print("clear dlg", seg["segment_id"]); vp["dialogue"]=[]; changed=True
if changed:
    Path(f"projects/{pid}/scripts/episode_3.json").write_text(json.dumps(script, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")

# only enqueue video for segments with storyboard file
ready=[]
for seg in script["segments"]:
    sid=seg["segment_id"]
    if Path(f"projects/{pid}/storyboards/scene_{sid}.png").exists():
        ready.append(seg)
    else:
        print("skip video no board", sid)

print("enqueue videos for", [s["segment_id"] for s in ready])
for seg in ready:
    sid=seg["segment_id"]
    if Path(f"projects/{pid}/videos/scene_{sid}.mp4").exists():
        print("video exists", sid); continue
    tok=auth(); wait_idle(tok, 60)
    payload={"prompt": seg["video_prompt"], "script_file":"episode_3.json", "duration_seconds": int(seg.get("duration_seconds") or 10)}
    H={"Authorization":f"Bearer {tok}","Content-Type":"application/json"}
    req=urllib.request.Request(f"{base}/generate/video/{sid}", data=json.dumps(payload, ensure_ascii=False).encode(), headers=H, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            r=json.load(resp)
        print("enqueued video", sid, r.get("task_id","")[:8])
    except Exception as ex:
        err=ex.read().decode("utf-8","replace") if hasattr(ex,"read") else str(ex)
        print("video enqueue err", sid, err[:400])
    time.sleep(1)

# poll videos
deadline=time.time()+50*60
while time.time()<deadline:
    tok=auth()
    data=json.load(urllib.request.urlopen(urllib.request.Request(f"{base}/tasks?page_size=80", headers={"Authorization":f"Bearer {tok}"}), timeout=30))
    items=[t for t in (data.get("items") or []) if t.get("task_type")=="video" and str(t.get("resource_id","")).startswith("E3S")]
    by_t={}
    for t in items:
        rid=t["resource_id"]
        if rid not in by_t or (t.get("updated_at") or "")>=(by_t[rid].get("updated_at") or ""):
            by_t[rid]=t
    statuses={k:v.get("status") for k,v in sorted(by_t.items())}
    mp4=list(Path(f"projects/{pid}/videos").glob("scene_E3S*.mp4")) if Path(f"projects/{pid}/videos").exists() else []
    active=sum(1 for s in statuses.values() if str(s).lower() in ("queued","running","pending"))
    done=sum(1 for s in statuses.values() if str(s).lower() in ("succeeded","completed","success"))
    failed=sum(1 for s in statuses.values() if str(s).lower() in ("failed","error"))
    line=f"{time.strftime('%H:%M:%S')} done={done} failed={failed} active={active} mp4={len(mp4)} {statuses}"
    print(line)
    with open(log,"a",encoding="utf-8") as f: f.write(line+"\n")
    if active==0 and len(statuses)>=len(ready):
        for rid,t in sorted(by_t.items()):
            if str(t.get("status")).lower() in ("failed","error"):
                print("FAIL", rid, (t.get("error_message") or "")[:250])
            else:
                print("OK", rid)
        break
    time.sleep(10)

# export jianying if all 10 videos
mp4n=len(list(Path(f"projects/{pid}/videos").glob("scene_E3S*.mp4"))) if Path(f"projects/{pid}/videos").exists() else 0
print("final boards", len(list(Path(f"projects/{pid}/storyboards").glob("scene_E3S*.png"))), "videos", mp4n)
if mp4n>=10:
    tok=auth()
    H={"Authorization":f"Bearer {tok}","Content-Type":"application/json"}
    req=urllib.request.Request(f"{base}/export/token?scope=current", data=b"{}", headers=H, method="POST")
    with urllib.request.urlopen(req, timeout=60) as resp:
        dl=json.load(resp)["download_token"]
    params=urllib.parse.urlencode({"episode":3,"jianying_version":"6","draft_path":r"C:\Users\19183\JianyingPro Drafts","download_token":dl})
    with urllib.request.urlopen(urllib.request.Request(f"{base}/export/jianying-draft?{params}", headers={"Authorization":f"Bearer {tok}"}), timeout=180) as resp:
        data=resp.read()
    out=Path(f"projects/{pid}/output/episode_3_jianying_draft.zip")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    print("jianying", out, round(len(data)/1024/1024,2), "MB")
