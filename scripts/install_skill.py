#!/usr/bin/env python3
"""Install the SLYCE skill (Slyce: PSD exporting assets and UI skill by Ali) for Claude Code on this machine (Windows / macOS).

  python install_skill.py          # copy this folder to ~/.claude/skills/slyce
  python install_skill.py --link   # link instead of copy (edits in this folder are picked up live)

Also copies SLYCE's three agents (agents/slyce-planner.md, slyce-executor.md, slyce-reviewer.md) into
~/.claude/agents, so Claude Code can start them by name. Agent files are always copied (re-run after edits).
"""
import glob
import os
import platform
import shutil
import subprocess
import sys

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DST = os.path.join(os.path.expanduser("~"), ".claude", "skills", "slyce")
AGENTS = os.path.join(os.path.expanduser("~"), ".claude", "agents")


def install_skill(link):
    os.makedirs(os.path.dirname(DST), exist_ok=True)
    if os.path.lexists(DST):
        if os.path.realpath(DST) == os.path.realpath(SRC):
            print("Skill already installed (points to this folder):", DST)
            return
        is_junction = getattr(os.path, "isjunction", lambda p: False)(DST)
        if os.path.islink(DST):
            os.unlink(DST)
        elif is_junction:
            os.rmdir(DST)  # removes the junction only, never the target
        else:
            shutil.rmtree(DST)  # an older copied install
    if link:
        if platform.system() == "Windows":
            subprocess.run(["cmd", "/c", "mklink", "/J", DST, SRC], check=True)  # junction: no admin rights needed
        else:
            os.symlink(SRC, DST)
    else:
        shutil.copytree(SRC, DST, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git"))
    print(("Linked " if link else "Copied ") + SRC + " -> " + DST)


def install_agents():
    os.makedirs(AGENTS, exist_ok=True)
    for f in sorted(glob.glob(os.path.join(SRC, "agents", "slyce-*.md"))):
        shutil.copy2(f, os.path.join(AGENTS, os.path.basename(f)))
        print("Agent " + os.path.basename(f) + " -> " + AGENTS)


def main():
    install_skill("--link" in sys.argv)
    install_agents()
    print("Restart Claude Code (or open a new session) to load the skill and its agents.")


if __name__ == "__main__":
    main()
