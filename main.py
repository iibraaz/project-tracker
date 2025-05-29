from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
from uuid import uuid4
import os
from dotenv import load_dotenv
from supabase import create_client
import openai

# Load env vars
load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Initialize clients
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
openai.api_key = OPENAI_API_KEY

# FastAPI setup
app = FastAPI(title="AI Project Assistant")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Pydantic models
class Task(BaseModel):
    title: str
    description: Optional[str] = ""
    status: Optional[str] = "pending"

class ProjectInput(BaseModel):
    user_id: str
    project_name: str
    project_goal: str
    num_phases: Optional[int] = None

class UpdateInput(BaseModel):
    project_id: str
    update_text: str
    type: str  # "daily" or "weekly"

# Routes
@app.post("/projects")
async def create_project(data: ProjectInput):
    # Generate GPT breakdown
    gpt_prompt = f"Break down the following project goal into phases: {data.project_goal}."
    if data.num_phases:
        gpt_prompt += f" Limit the breakdown to {data.num_phases} phases."

    response = openai.ChatCompletion.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": "You are a helpful assistant that structures projects."},
            {"role": "user", "content": gpt_prompt}
        ]
    )
    plan = response["choices"][0]["message"]["content"]

    # Save to Supabase
    project_id = str(uuid4())
    supabase.table("projects").insert({
        "id": project_id,
        "user_id": data.user_id,
        "name": data.project_name,
        "goal": data.project_goal,
        "plan": plan
    }).execute()

    return {"project_id": project_id, "plan": plan}

@app.post("/updates")
async def submit_update(update: UpdateInput):
    # Generate weekly report analysis
    if update.type == "weekly":
        prompt = f"Analyze the following weekly update and return needs, issues, and progress separately:\n{update.update_text}"
        response = openai.ChatCompletion.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "You are a smart project analyst."},
                {"role": "user", "content": prompt}
            ]
        )
        summary = response["choices"][0]["message"]["content"]
    else:
        summary = update.update_text

    # Save to Supabase
    supabase.table("updates").insert({
        "project_id": update.project_id,
        "type": update.type,
        "original": update.update_text,
        "summary": summary
    }).execute()

    return {"summary": summary}

@app.post("/upload")
async def upload_document(file: UploadFile = File(...), project_id: str = Form(...)):
    # Upload to Supabase storage
    file_bytes = await file.read()
    file_path = f"documents/{project_id}/{file.filename}"
    supabase.storage.from_("documents").upload(file_path, file_bytes)
    public_url = supabase.storage.from_("documents").get_public_url(file_path).get("publicURL")

    # Save to DB
    supabase.table("documents").insert({
        "project_id": project_id,
        "file_name": file.filename,
        "url": public_url
    }).execute()

    return {"url": public_url}

@app.get("/")
async def root():
    return {"message": "AI Project Assistant API is running."}
