#!/usr/bin/env python3
"""
Sanity check: resume_parser.py + OpenRouter.
Usage: python scripts/sanity_parser.py [path/to/resume]
Default file: data/cv_pm.md
"""

import os
import sys
from pathlib import Path

# Load .env before anything else
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

sys.path.insert(0, str(Path(__file__).parent.parent))

from openai import OpenAI
from onboarding.resume_parser import ResumeParser

BASE_DIR = Path(__file__).parent.parent

api_key = os.getenv("LLM_API_KEY")
if not api_key:
    print("ERROR: LLM_API_KEY not set in .env")
    sys.exit(1)

resume_file = Path(sys.argv[1]) if len(sys.argv) > 1 else BASE_DIR / "data" / "cv_pm.md"
if not resume_file.exists():
    print(f"ERROR: file not found: {resume_file}")
    sys.exit(1)

client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=api_key,
)

print(f"Parsing: {resume_file.name}")
print(f"  TEXT_MODEL  = {os.getenv('LLM_MODEL', 'anthropic/claude-3-5-haiku')} (this file)")
print(f"  MULTIMODAL  = {os.getenv('RESUME_PARSE_MODEL', 'google/gemini-2.0-flash-001')} (PDF/img)")
print()

parser = ResumeParser(client)
data = parser.parse_file(resume_file)

print(f"completeness: {data.completeness:.0%}")
print(f"name:         {data.name}")
print(f"role:         {data.role}")
print(f"experience:   {data.experience_years} years")
print(f"company:      {data.current_company}")
print(f"domain:       {data.domain}")
print(f"skills:       {data.skills}")
print(f"achievements: {data.achievements}")
print(f"key_cases:    {data.key_cases}")
print(f"tools:        {data.tools}")
print(f"languages:    {data.languages}")

if data.hints:
    print("\nhints:")
    for h in data.hints:
        print(f"  - {h}")

print("\n--- resume_facts.md preview ---")
print(parser.to_md(data))
