import os
import json
import schedule
import time
import requests
import pandas as pd
from datetime import datetime, timezone
from dotenv import load_dotenv
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, MetaData, Table, cast
from sqlalchemy.dialects.postgresql import JSONB, ARRAY
from google import genai

# --- 1. SETUP & CONFIG ---
load_dotenv()

# Database Connection
engine = create_engine(os.getenv("SUPABASE_URL"))
metadata_obj = MetaData()

# Define the table for SQLAlchemy
raw_signals = Table(
    'raw_signals', metadata_obj,
    Column('id', Integer, primary_key=True),
    Column('source', String(50)),
    Column('ticker', ARRAY(String)),
    Column('title', Text),
    Column('text', Text),
    Column('created_at', DateTime(timezone=True)),
    Column('ingested_at', DateTime(timezone=True), default=datetime.now(timezone.utc)),
    Column('metadata', JSONB)
)


gemini_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

# --- 2. VALIDATION LAYER ---
# Download the CSV from nasdaq.com/market-activity/stocks/screener
def load_nasdaq_tickers(filepath="nasdaq_screener.csv"):
    try:
        df = pd.read_csv(filepath)
        # O(1) lookup set
        return set(df['Symbol'].astype(str).str.strip().str.upper().tolist())
    except FileNotFoundError:
        print("Warning: nasdaq_screener.csv not found. Ticker validation will be skipped.")
        return set()

VALID_TICKERS = load_nasdaq_tickers()

# --- 3. LLM TICKER EXTRACTION ---
def extract_tickers_with_gemini(text_content):
    if not text_content or len(text_content.strip()) == 0:
        return []
        
    prompt = f"""
    Extract stock tickers and company names from this text. 
    Return JSON only in this exact format: {{"tickers": [], "companies": []}}.
    Text: {text_content}
    """
    try:
        # THE NEW GEMINI 2.5 FLASH CALL
        response = gemini_client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
        )
        clean_json = response.text.replace("```json", "").replace("```", "").strip()
        data = json.loads(clean_json)
        
        extracted_tickers = data.get("tickers", [])
        validated_tickers = []
        for t in extracted_tickers:
            t = t.upper().replace("$", "")
            if not VALID_TICKERS or t in VALID_TICKERS:
                validated_tickers.append(t)
            else:
                validated_tickers.append(f"{t}_UNCERTAIN")
                
        return validated_tickers
    except Exception as e:
        print(f"Gemini Extraction Error: {e}")
        return []

# --- 4. DATA INGESTION FUNCTIONS ---
def scrape_reddit():
    print(f"[{datetime.now()}] Scraping Reddit via JSON backdoor...")
    
    # You can scrape multiple subreddits at once by combining them with a +
    url = "https://www.reddit.com/r/wallstreetbets+stocks+investing+pennystocks/new.json?limit=10"
    
    # CRITICAL: Reddit will block you with a 429 error if you don't use a highly specific custom User-Agent
    headers = {
        "User-Agent": "macOS:TrAIdeEngine:v1.0 (by /u/Blanche_v17)" 
    }
    
    try:
        response = requests.get(url, headers=headers)
        
        if response.status_code == 200:
            data = response.json()
            posts = data.get("data", {}).get("children", [])
            
            with engine.begin() as conn:
                for post in posts:
                    post_data = post["data"]
                    title = post_data.get("title", "")
                    text = post_data.get("selftext", "")
                    subreddit = post_data.get("subreddit", "")
                    
                    combined_text = f"{title} {text}"
                    tickers = extract_tickers_with_gemini(combined_text)
                    
                    ins = raw_signals.insert().values(
                        source=f"reddit_{subreddit}",
                        ticker=tickers,
                        title=title,
                        text=text,
                        created_at=datetime.fromtimestamp(post_data.get("created_utc", 0), tz=timezone.utc),
                        metadata={
                            "score": post_data.get("score", 0),
                            "num_comments": post_data.get("num_comments", 0),
                            "url": f"https://reddit.com{post_data.get('permalink', '')}"
                        }
                    )
                    conn.execute(ins)
            print("Successfully ingested Reddit batch.")
        else:
             print(f"Reddit blocked the request. Status Code: {response.status_code}")
             
    except Exception as e:
        print(f"Error scraping Reddit: {e}")

def scrape_polygon_news():
    print(f"[{datetime.now()}] Pulling Polygon News...")
    api_key = os.getenv("POLYGON_API_KEY")
    url = f"https://api.polygon.io/v2/reference/news?limit=10&apiKey={api_key}"
    
    try:
        response = requests.get(url)
        data = response.json()
        
        if "results" in data:
            with engine.begin() as conn:
                # 1. Create an empty list to hold all rows
                bulk_data = [] 
                
                # 2. Package all the articles into dictionaries
                for article in data["results"]:
                    bulk_data.append({
                        "source": "polygon_news",
                        "ticker": article.get("tickers", []),
                        "title": article.get("title", ""),
                        "text": article.get("description", ""),
                        "created_at": datetime.strptime(article["published_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc),
                        "metadata": {
                            "publisher": article.get("publisher", {}).get("name", "Unknown"),
                            "article_url": article.get("article_url", "")
                        }
                    })
                
                # 3. Fire them ALL to Supabase in one single network trip
                if bulk_data:
                    conn.execute(raw_signals.insert(), bulk_data)
                    
            print("Successfully bulk-ingested Polygon News to Supabase.")
    except Exception as e:
         print(f"Error pulling Polygon news: {e}")

# Note: Unusual Whales / Market Chameleon scraping is heavily dependent on 
# their specific API access or HTML structure if web scraping. 
# Placeholder function for architecture completeness:
def scrape_options_flow():
    print(f"[{datetime.now()}] Scraping Options Flow (Placeholder)...")
    pass 

# --- 5. SCHEDULING ---
# Wire up the timing defined in the instructions
schedule.every(5).minutes.do(scrape_reddit)
schedule.every(15).minutes.do(scrape_polygon_news)
schedule.every(15).minutes.do(scrape_options_flow)

if __name__ == "__main__":
    print("Starting Ingestion Engine...")
    
    # Run once immediately on startup
    scrape_reddit()
    scrape_polygon_news()
    
    while True:
        schedule.run_pending()
        time.sleep(1)