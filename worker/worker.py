import httpx
from arq import worker
from core.config import settings


async def process_pull_request_review(ctx: dict, job_payload: dict):
    # 1. Extracting pr_number, repo_name, repo_owner, and diff_url from job_payload.
    pr_number = job_payload["pr_number"]
    repo_name = job_payload["repo_name"]
    repo_owner = job_payload["repo_owner"]
    diff_url = job_payload["diff_url"]

    headers = {
        # 1. Tell GitHub who is asking for the data (Token Auth)
        "Authorization": f"Bearer {settings.GITHUB_TOKEN}",
        # 2. Tell GitHub to return a raw text diff, NOT a JSON object
        "Accept": "application/vnd.github.v3.diff",
        "User-Agent": "AI-Code-Reviewer-Platform",
    }

    # initializing an async httpx async client
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            print(
                f"🔄 [Job {ctx.get('job_id')}] Fetching diff for {repo_owner}/{repo_name} PR #{pr_number}..."
            )

            response = await client.get(diff_url, headers=headers)
            raw_diff_text = response.text

            print(f"Successfully fetched PR diff ({len(raw_diff_text)}) characters")

            return {"status": "success", "diff_length": len(raw_diff_text)}

        except httpx.HTTPStatusError as exc:
            print(f"server error while fetching diff : {exc.response.status_code}")

            if exc.response.status_code in [500, 502, 503, 504]:
                if ctx.get("job-try", 1) < 3:
                    print("Retrying the job in 5 seconds!")
                    raise Retry(defer=5)

        except httpx.RequestException as exc:
            # Handles low-level network issues like DNS failures or connection timeouts
            print(f"network transport connectivity issue {exc}")
            if ctx.get("job_try", 1) < 3:
                print("Retrying the job in 10 seconds!")
                raise Retry(defer=5)

            raise Exception("max retry limit exceeded in the network")


# ARQ worker setup
class WorkerSettings:
    functions = [process_pull_request_review]
    redis_settings = worker.RedisSettings.from_dsn(settings.REDIS_URL)


if __name__ == "__main__":
    from arq import run_worker

    run_worker(WorkerSettings)
