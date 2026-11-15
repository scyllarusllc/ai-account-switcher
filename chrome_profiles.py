#!/usr/bin/env python3
"""Named Chrome profiles with isolated data paths and launch parameters."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from storage import DEFAULT_DB, read_config

DEFAULT_CONFIG = DEFAULT_DB

def chrome_binary(config):
    explicit = config.get("chrome_binary") or os.environ.get("CHROME_BINARY")
    candidates = [explicit] if explicit else [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        shutil.which("google-chrome"), shutil.which("chromium"),
        str(Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Google/Chrome/Application/chrome.exe"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(Path(candidate).resolve())
    raise ValueError("找不到 Chrome，请设置 chrome_binary 或 CHROME_BINARY")

def build_command(config, name, urls, extra):
    profiles = config["profiles"]
    if name not in profiles:
        raise ValueError(f"未知配置：{name}")
    profile = profiles[name]
    if not isinstance(profile, dict) or not profile.get("user_data_dir"):
        raise ValueError("每个配置必须指定 user_data_dir")
    directory = str(Path(os.path.expandvars(profile["user_data_dir"])).expanduser().resolve())
    command = [chrome_binary(config), f"--user-data-dir={directory}"]
    for key, flag in [("profile_directory", "profile-directory"), ("proxy", "proxy-server"), ("proxy_bypass_list", "proxy-bypass-list")]:
        value = profile.get(key)
        if value:
            if not isinstance(value, str):
                raise ValueError(f"{key} 必须为字符串")
            command.append(f"--{flag}={value}")
    args = profile.get("args", [])
    if not isinstance(args, list) or not all(isinstance(arg, str) and arg.startswith("--") for arg in args):
        raise ValueError("args 必须为以 -- 开头的参数字符串数组")
    for arg in args + extra:
        if arg.split("=", 1)[0] in {"--user-data-dir", "--profile-directory", "--proxy-server", "--proxy-bypass-list"}:
            raise ValueError("请通过配置字段设置目录和代理，避免重复参数")
    command.extend(args)
    command.extend(extra)
    targets = urls or profile.get("urls", [])
    if not isinstance(targets, list) or not all(isinstance(url, str) and url.startswith(("https://", "http://")) for url in targets):
        raise ValueError("urls 必须为 http:// 或 https:// URL 数组")
    command.extend(targets)
    return command

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("list", help="列出配置")
    launch = sub.add_parser("launch", help="启动指定配置")
    launch.add_argument("name")
    launch.add_argument("urls", nargs="*")
    launch.add_argument("--dry-run", action="store_true")
    launch.add_argument("--arg", action="append", default=[], help="额外参数，使用 --arg=--flag=value")
    options = parser.parse_args()
    try:
        config = read_config(options.config.expanduser())
        if options.action == "list":
            for name, profile in config["profiles"].items():
                print(f"{name}: {profile.get('user_data_dir', '')}  proxy={profile.get('proxy') or '系统默认'}")
            return 0
        command = build_command(config, options.name, options.urls, options.arg)
        if options.dry_run:
            print(json.dumps(command, ensure_ascii=False, indent=2))
        else:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            print(f"已发出启动请求：{options.name} (PID {process.pid})")
        return 0
    except (OSError, ValueError, TypeError, KeyError) as error:
        print(f"错误：{error}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    sys.exit(main())
