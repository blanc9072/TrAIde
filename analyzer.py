import os
from datetime import datetime
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
import anthropic

# --- 1. SETUP ---
load_dotenv()

# Connect to your existing Supabase database
engine = create_engine(os.getenv("SUPABASE_URL"))

# Initialize Claude Client
claude = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

# --- 2. DATA AGGREGATION ---
def get_ticker_data(ticker, hours_back=72):
    print(f"Pulling {ticker} data from the last {hours_back} hours...")
    
    # We use raw SQL to easily check if the requested ticker is inside the array column
    query = text("""
        SELECT source, title, text, created_at 
        FROM raw_signals 
        WHERE :ticker = ANY(ticker) 
        AND created_at >= NOW() - INTERVAL '1 hour' * :hours
        ORDER BY created_at ASC
    """)
    
    with engine.connect() as conn:
        results = conn.execute(query, {"ticker": ticker, "hours": hours_back}).fetchall()
        
    if not results:
        return None
        
    # Format the scattered rows into one massive, readable text block for Claude
    formatted_text = f"--- RAW DATA FOR {ticker} ---\n\n"
    for row in results:
        source, title, content, created_at = row
        formatted_text += f"[{created_at}] Source: {source}\nTitle: {title}\nContent: {content}\n\n"
        
    return formatted_text, len(results)

# --- 3. CLAUDE ANALYSIS ---
def analyze_with_claude(ticker, context_data):
    print(f"Feeding data to Claude Sonnet for deep analysis...")
    
    # The System Prompt: This dictates exactly how Claude behaves and structures its response
    prompt = f"""
    You are an elite quantitative financial analyst. 
    I am providing you with the last 72 hours of raw Reddit sentiment and Polygon news articles mentioning {ticker}.
    
    Analyze the overall retail sentiment, identify any shifts in momentum, and evaluate recent news catalysts. 
    Return your analysis in exactly this format:
    
    VERDICT: [Bullish / Bearish / Neutral]
    CONFIDENCE: [1-10]
    KEY DRIVERS: 
    - [Bullet point 1]
    - [Bullet point 2]
    SUMMARY: [A short 2-3 sentence summary of the setup]
    
    Raw Data:
    {context_data}
    """
    
    try:
        # Calling the latest Sonnet 3.5 model
        response = claude.messages.create(
            model="claude-sonnet-4-5", 
            max_tokens=1000,
            temperature=0.2, # Low temperature keeps the analysis grounded and analytical
            messages=[
                {"role": "user", "content": prompt}
            ]
        )
        return response.content[0].text
    except Exception as e:
        return f"Error contacting Claude: {e}"

# --- 4. EXECUTION ---
if __name__ == "__main__":
    # You can change this ticker to any stock you want to analyze
    target_ticker = "NVDA"  
    
    # 1. Pull the data
    data_tuple = get_ticker_data(target_ticker, hours_back=72)
    
    if data_tuple:
        raw_text, data_count = data_tuple
        print(f"Found {data_count} data points for {target_ticker}.")
        
        # 2. Analyze the data
        analysis = analyze_with_claude(target_ticker, raw_text)
        
        # 3. Print the final signal
        print("\n" + "="*50)
        print(analysis)
        print("="*50)
    else:
        print(f"No data found for {target_ticker} in the specified timeframe.")