from fastapi import FastAPI, Request, HTTPException, Header, Depends
from core.config import settings
import hmac
from contextlib import asynccontextmanager
import hashlib
from pydantic import BaseModel
from arq import create_pool
from arq.connections import RedisSettings

app = FastAPI(title="AI Code Reviewer Gateway")


# lifespan management
@asynccontextmanager
async def lifespan():

    # app lifespan starts here
    app.state.redis_pool = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
    print("Redis connection pool successfully initialized for Arq!")

    yield

    # app lifespan gets closed here
    await app.state.redis_pool.close()


class GitHubWebhookPayload(BaseModel):
    """defines the strict pydantic schema contract for the incoming github data"""

    action: str
    number: int
    repository: dict
    pull_request: dict


async def verify_github_signature(
    request: Request, x_hub_signature_256: str = Header(None)
):
    if not x_hub_signature_256:
        raise HTTPException(
            status_code=401,
            detail="signature validation failed: x_hub_signature_256 does'nt exist",
        )

    body = await request.body()

    secret_bytes = settings.GITHUB_WEBHOOK_SECRET.encode()

    hash_maker = hmac.new(secret_bytes, body, hashlib.sha256)
    computed_signature = hash_maker.hexdigest()

    parts = x_hub_signature_256.split("=")

    if len(parts) != 2 or parts[0] != "sha256":
        raise HTTPException(status_code=400, detail="Invalid signature format")

    github_signature = parts[1]

    if not hmac.compare_digest(computed_signature, github_signature):
        raise HTTPException(
            status_code=401,
            detail="signature validation failed : signatures do not match.",
        )


@app.post("/webhook")
async def github_webhook(
    payload: GitHubWebhookPayload, _=Depends(verify_github_signature)
):
    # feature A : we only care if a action is opened or updated with new code
    if payload.action not in ["opened", "synchronized", "reopened"]:
        return {
            status: "ignored",
            message: f"action {payload.action} skipped, no review required.",
        }

    # Feature B: Isolate the minimal payload metadata our AI worker will need
    job_payload = {
        "pr_number": payload.number,
        "repo_name": payload.repository.get("name"),
        "repo_owner": payload.repository.get("owner", {}).get("login"),
        "diff_url": payload.pull_request.get("diff_url"),
    }

    # Feature C: Dispatch the job asynchronously to our Redis queue
    # 'process_pull_request_review' is the exact name of the function our worker will run later
    await app.state.redis_pool.enqueue_job("process_pull_request_review", job_payload)

    return {
        "status": "enqueued accomplished",
        "message": f"successfully scheduled AI review task for PR #{payload.number}",
    }
