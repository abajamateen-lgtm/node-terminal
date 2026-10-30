import json
import urllib.request
from apify import Actor

async def main():
    async with Actor:
        Actor.log.info("Fetching jobs from Remote OK...")

        url = "https://remoteok.com/api"
        req = urllib.request.Request(
            url, 
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        )

        response = urllib.request.urlopen(req)
        data = json.loads(response.read().decode('utf-8'))

        jobs = data[1:]
        cleaned_jobs = []

        for job in jobs:
            if not isinstance(job, dict):
                continue

            cleaned_job = {
                "id": job.get("id"),
                "position": job.get("position"),
                "company": job.get("company"),
                "location": job.get("location") or "Worldwide",
                "tags": job.get("tags", []),
                "url": job.get("url"),
                "date": job.get("date"),
                "salary_min": job.get("salary_min"),
                "salary_max": job.get("salary_max")
            }
            cleaned_jobs.append(cleaned_job)

        Actor.log.info(f"Successfully scraped {len(cleaned_jobs)} jobs!")
        await Actor.push_data(cleaned_jobs)
