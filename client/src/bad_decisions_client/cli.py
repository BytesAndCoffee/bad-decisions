from __future__ import annotations
import argparse, json, os, sys, tempfile, uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from . import __version__
from .together import TogetherClient, load_session, run_together, save_session
DEFAULT_API_URL = "https://bytes.coffee/bad-decisions"
CONFIG_PATH = Path.home() / ".regret.env"
ROUND_PATH = Path.home() / ".regret-last-round.json"
CONSEQUENCES_SCHEMA_VERSION = 1
UPDATE_CHECK_SECONDS = 24 * 60 * 60

def _read_config(path: Path | None = None) -> dict[str,str]:
    path = CONFIG_PATH if path is None else path
    try: lines = path.read_text(encoding="utf-8").splitlines()
    except OSError: return {}
    result = {}
    for line in lines:
        if "=" in line and not line.lstrip().startswith("#"):
            key,value=line.split("=",1)
            if key.replace("_","").isalnum(): result[key]=value
    return result

def _atomic_json(path: Path, value: dict[str,Any]) -> None:
    _atomic_text(path, json.dumps(value))

def _atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd,name=tempfile.mkstemp(prefix=f".{path.name}.",dir=path.parent)
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as f: f.write(value); f.flush(); os.fsync(f.fileno())
        os.chmod(name,0o600); os.replace(name,path)
    finally:
        try: os.unlink(name)
        except FileNotFoundError: pass

def _write_config(value: dict[str,str]) -> None:
    _atomic_text(CONFIG_PATH, "\n".join(f"{key}={item}" for key,item in sorted(value.items())) + "\n")

def _consequences_enabled(config: dict[str, str]) -> bool:
    return config.get("REGRET_CONSEQUENCES") == "enjoy" and config.get("REGRET_CONSEQUENCES_SCHEMA") == str(CONSEQUENCES_SCHEMA_VERSION)

def _set_consequences(value: str) -> None:
    config = _read_config()
    config["REGRET_CONSEQUENCES"] = value
    config["REGRET_CONSEQUENCES_SCHEMA"] = str(CONSEQUENCES_SCHEMA_VERSION)
    _write_config(config)

def _prompt_consequences() -> dict[str, str]:
    config = _read_config()
    if config.get("REGRET_CONSEQUENCES_SCHEMA") == str(CONSEQUENCES_SCHEMA_VERSION) and config.get("REGRET_CONSEQUENCES") in {"enjoy", "regret"}:
        return config
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return config
    print("[Y] Enjoy Consequences: enable voting and stable-ID pseudonymous vote telemetry", file=sys.stderr)
    print("[n] Regret Consequences: disable voting entirely", file=sys.stderr)
    choice = input("Enjoy Consequences? [Y/n] ").strip().lower()
    _set_consequences("enjoy" if choice in {"y", "yes"} else "regret")
    print("You can enable or disable Consequences at any time. We're not Facebook.", file=sys.stderr)
    return _read_config()

def _consequences(argv: Sequence[str]) -> int:
    parser_ = argparse.ArgumentParser(prog="regret consequences")
    parser_.add_argument("action", choices=("status", "enjoy", "regret", "enable", "disable"))
    args = parser_.parse_args(argv)
    if args.action in {"enjoy", "enable", "regret", "disable"}:
        value = "enjoy" if args.action in {"enjoy", "enable"} else "regret"
        try:
            _set_consequences(value)
        except OSError as exc:
            print(f"regret: cannot save Consequences preference: {exc}", file=sys.stderr)
            return 1
        if value == "enjoy":
            print("Consequences enjoyed.\nVoting enabled.\nThe stew remembers, pseudonymously.")
        else:
            print("Consequences regretted.\nVoting disabled.\nYour decisions are now between you and the stew.")
        return 0
    config = _read_config()
    print("Consequences enabled" if _consequences_enabled(config) else "Consequences disabled")
    return 0

def _maybe_update_check(config: dict[str, str], api_url: str) -> None:
    current = __version__
    try:
        last = datetime.fromisoformat(config.get("REGRET_LAST_UPDATE_CHECK", "")).timestamp()
    except (ValueError, TypeError, OverflowError):
        last = 0
    now = datetime.now(timezone.utc)
    if now.timestamp() - last < UPDATE_CHECK_SECONDS:
        latest = config.get("REGRET_LATEST_SEEN_VERSION")
    else:
        try:
            payload, _ = _request_json(f"{_base_url(api_url)}/healthz", timeout=2.0)
            latest = payload.get("version")
            if latest:
                config["REGRET_LATEST_SEEN_VERSION"] = str(latest)
            config["REGRET_LAST_UPDATE_CHECK"] = now.isoformat()
            _write_config(config)
        except (RuntimeError, OSError, KeyError, TypeError, AttributeError, ValueError):
            return
    if latest:
        try:
            from packaging.version import Version
            newer = Version(str(latest)) > Version(str(current))
        except Exception:
            newer = False
        if newer:
            print(f"A newer Bad Decision is available:\nregret {current} -> {latest}", file=sys.stderr)


def _client_id(reset: bool = False) -> str | None:
    config=_read_config()
    if not _consequences_enabled(config): return None
    if config.get("REGRET_ANALYTICS_IDENTITY","on").lower() == "off": return None
    value=config.get("REGRET_CLIENT_ID")
    try: parsed=str(uuid.UUID(value)) if value and not reset else str(uuid.uuid4())
    except ValueError: parsed=str(uuid.uuid4())
    if parsed != value:
        config["REGRET_CLIENT_ID"]=parsed
        try: _write_config(config)
        except OSError: pass
    return parsed

def parser() -> argparse.ArgumentParser:
    result=argparse.ArgumentParser(description="Deal a hand from a Bad Decisions service.")
    result.add_argument("--api-url",default=DEFAULT_API_URL); result.add_argument("--packs"); result.add_argument("--black-packs"); result.add_argument("--white-packs")
    result.add_argument("--list-packs",action="store_true"); result.add_argument("--health",action="store_true"); result.add_argument("--json",action="store_true"); result.add_argument("--timeout",type=float,default=10.0)
    return result

def _base_url(value: str) -> str:
    url=value.strip().rstrip("/")
    if not url.startswith(("http://","https://")): raise ValueError("--api-url must start with http:// or https://")
    return url

def _request_json(url: str, *, timeout: float, method: str="GET", payload: dict[str,Any] | None=None, headers: dict[str,str] | None=None) -> tuple[Any,Any]:
    base={"Accept":"application/json","User-Agent":f"bad-decisions-client/{__version__}"}
    if headers: base.update(headers)
    data=None if payload is None else json.dumps(payload).encode("utf-8")
    if data: base["Content-Type"]="application/json"
    request=Request(url,data=data,method=method,headers=base)
    try:
        with urlopen(request,timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8")), getattr(response, "headers", {})
    except HTTPError as exc:
        try:
            body=json.loads(exc.read().decode("utf-8")); error=body.get("error",{}); raise RuntimeError(f"{error.get('code')}: {error.get('message')}" if error.get("code") else error.get("message",f"HTTP {exc.code}")) from exc
        except (UnicodeDecodeError,json.JSONDecodeError): raise RuntimeError(f"HTTP {exc.code}: {exc.reason}") from exc
    except (URLError,TimeoutError) as exc: raise RuntimeError(f"Cannot reach API: {exc.reason if isinstance(exc,URLError) else exc}") from exc

def _print_packs(packs: list[dict[str,Any]]) -> None:
    for pack in packs:
        counts=pack.get("counts",{}); print(f"{pack['id']}\t{pack['name']}\tblack={counts.get('black',0)}\twhite={counts.get('white',0)}")

def _feedback(argv: Sequence[str]) -> int:
    command=argparse.ArgumentParser(prog="regret feedback")
    command.add_argument("choice",choices=("enjoy","regret","clear")); command.add_argument("--api-url",default=DEFAULT_API_URL); command.add_argument("--timeout",type=float,default=10)
    args=command.parse_args(argv)
    if not _consequences_enabled(_read_config()):
        print("regret: no saved round with feedback available (Consequences is disabled; run `regret consequences enjoy` first)", file=sys.stderr)
        return 1
    try:
        saved=json.loads(ROUND_PATH.read_text(encoding="utf-8"))
        if not isinstance(saved,dict) or not all(key in saved for key in ("url","token")): raise ValueError
    except (OSError,json.JSONDecodeError,TypeError,ValueError):
        print("regret: no saved round with feedback available",file=sys.stderr)
        return 1
    try:
        method="DELETE" if args.choice=="clear" else "PUT"; body=None if method=="DELETE" else {"enjoyed":args.choice=="enjoy"}
        _request_json(_base_url(args.api_url)+saved["url"],timeout=args.timeout,method=method,payload=body,headers={"X-Regret-Feedback-Token":saved["token"]})
        print("feedback cleared" if args.choice=="clear" else f"marked {args.choice}")
        return 0
    except (RuntimeError,ValueError,KeyError) as exc: print(f"regret: {exc}",file=sys.stderr); return 1

def _provenance(argv: Sequence[str]) -> int:
    command=argparse.ArgumentParser(prog="regret provenance")
    command.add_argument("--json",action="store_true")
    args=command.parse_args(argv)
    try:
        saved=json.loads(ROUND_PATH.read_text(encoding="utf-8"))
        provenance=saved["provenance"]
        if not isinstance(provenance,dict) or not provenance: raise ValueError("saved round has no provenance")
    except (OSError,json.JSONDecodeError,KeyError,TypeError,ValueError) as exc:
        detail=f": {exc}" if not isinstance(exc,OSError) else ""
        print(f"regret: no saved round provenance available{detail}",file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(provenance,ensure_ascii=False,indent=2,sort_keys=True))
        return 0
    for index,pack_id in enumerate(sorted(provenance)):
        details=provenance[pack_id]
        if not isinstance(details,dict):
            print("regret: saved round provenance is malformed",file=sys.stderr)
            return 1
        if index: print()
        print(pack_id)
        print(f"  License: {details.get('license_id','unknown')}")
        if details.get("license_url"): print(f"  License URL: {details['license_url']}")
        print(f"  Attribution: {details.get('attribution','unknown')}")
        print(f"  Version: {details.get('version','unknown')}")
        sources=details.get("sources",[])
        if sources:
            print("  Sources:")
            for source in sources:
                if not isinstance(source,dict):
                    print("regret: saved round provenance is malformed",file=sys.stderr)
                    return 1
                print(f"    - {source.get('origin','unknown')}")
                for label,key in (("Edition","edition"),("SHA-256","sha256"),("Retrieved","retrieved"),("License evidence","license_evidence")):
                    if source.get(key): print(f"      {label}: {source[key]}")
    return 0

def _identity(argv: Sequence[str]) -> int:
    p=argparse.ArgumentParser(prog="regret identity"); p.add_argument("action",choices=("status","reset","off","on")); args=p.parse_args(argv)
    config=_read_config()
    if args.action=="off": config["REGRET_ANALYTICS_IDENTITY"]="off"
    elif args.action=="on": config["REGRET_ANALYTICS_IDENTITY"]="on"
    elif args.action=="reset": config["REGRET_ANALYTICS_IDENTITY"]="on"; config["REGRET_CLIENT_ID"]=str(uuid.uuid4())
    if args.action != "status":
        try: _write_config(config)
        except OSError as exc: print(f"regret: cannot save identity: {exc}",file=sys.stderr); return 1
    print("identity omitted" if config.get("REGRET_ANALYTICS_IDENTITY")=="off" else "identity enabled")
    return 0

def _together(argv: Sequence[str]) -> int:
    command=argparse.ArgumentParser(prog="regret together",description="Give in to Peer Pressure.")
    command.add_argument("room"); command.add_argument("--name"); command.add_argument("--api-url",default=DEFAULT_API_URL)
    command.add_argument("--timeout",type=float,default=10.0); command.add_argument("--heartbeat",type=float,default=5.0)
    args=command.parse_args(argv)
    if args.timeout<=0: command.error("--timeout must be greater than zero")
    if args.heartbeat<=0: command.error("--heartbeat must be greater than zero")
    try: base=_base_url(args.api_url)
    except ValueError as exc: print(f"regret: {exc}",file=sys.stderr); return 1
    saved=load_session(base,args.room)
    config=_read_config()
    name=args.name or (saved or {}).get("display_name") or config.get("REGRET_DISPLAY_NAME")
    if not name and sys.stdin.isatty(): name=input("What should the table call you? ").strip()
    if not name:
        print("regret: --name is required when no saved room session exists",file=sys.stderr)
        return 1
    client=TogetherClient(base,args.room,args.timeout,_request_json)
    try:
        joined=client.join(name,saved)
        save_session(base,args.room,{"player_id":joined["player_id"],"session_token":joined["session_token"],"display_name":name},_atomic_json)
        return run_together(client,heartbeat_interval=args.heartbeat)
    except (RuntimeError,OSError,KeyError,TypeError,ValueError) as exc:
        print(f"regret: Peer Pressure unavailable: {exc}",file=sys.stderr)
        return 1

def run(argv: Sequence[str] | None=None) -> int:
    values=list(sys.argv[1:] if argv is None else argv)
    if values and values[0]=="feedback": return _feedback(values[1:])
    if values and values[0]=="provenance": return _provenance(values[1:])
    if values and values[0]=="identity": return _identity(values[1:])
    if values and values[0]=="consequences": return _consequences(values[1:])
    if values and values[0]=="together": return _together(values[1:])
    if values and values[0]=="health": values[0]="--health"
    elif values and values[0]=="deal": values.pop(0)
    args=parser().parse_args(values)
    config = _prompt_consequences() if not (args.health or args.list_packs or args.json) else _read_config()
    if args.timeout<=0: parser().error("--timeout must be greater than zero")
    if args.list_packs and args.health: parser().error("--list-packs and --health cannot be combined")
    try:
        base=_base_url(args.api_url)
        if args.health:
            payload,_=_request_json(f"{base}/healthz",timeout=args.timeout); print(json.dumps(payload,ensure_ascii=False,indent=2) if args.json else payload["status"]); return 0
        if args.list_packs:
            payload,_=_request_json(f"{base}/v1/packs",timeout=args.timeout)
            if args.json: print(json.dumps(payload,ensure_ascii=False,indent=2))
            else: _print_packs(payload)
            return 0
        params={k:v for k,v in {"packs":args.packs,"black_packs":args.black_packs,"white_packs":args.white_packs}.items() if v is not None}
        headers={}; client_id=_client_id()
        if client_id: headers={"X-Regret-Client-ID":client_id,"X-Regret-Session-ID":str(uuid.uuid4())}
        payload,response_headers=_request_json(f"{base}/v1/round"+(f"?{urlencode(params)}" if params else ""),timeout=args.timeout,headers=headers)
        feedback=payload.get("feedback",{}) if _consequences_enabled(config) else {}
        token=response_headers.get("X-Regret-Feedback-Token")
        saved={"provenance":payload["provenance"]} if isinstance(payload.get("provenance"),dict) else None
        if saved is not None:
            if token and feedback.get("available"):
                saved.update({"round_id":payload["round_id"],"url":feedback["url"],"token":token,"expires_at":feedback.get("expires_at")})
            try: _atomic_json(ROUND_PATH,saved)
            except OSError: pass
        if sys.stdin.isatty() and sys.stdout.isatty(): _maybe_update_check(config, args.api_url)
        print(json.dumps(payload,ensure_ascii=False,indent=2) if args.json else payload["result"]); return 0
    except (RuntimeError,ValueError,KeyError) as exc: print(f"regret: {exc}",file=sys.stderr); return 1

def main() -> int: return run()
def legacy_main() -> int: return run()
if __name__ == "__main__": raise SystemExit(main())
