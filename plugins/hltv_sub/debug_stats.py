import asyncio
import sys
import os
import re
from bs4 import BeautifulSoup
from curl_cffi.requests import AsyncSession

async def fetch(url):
    try:
        async with AsyncSession(impersonate="chrome") as session:
            print(f"Fetching {url}...")
            resp = await session.get(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Referer": "https://www.hltv.org/"
            }, timeout=30)
            return resp.text
    except Exception as e:
        print(f"Error fetching {url}: {e}")
        return None

async def main():
    base_url = "https://www.hltv.org"
    
    # 1. 获取最近的一个比赛结果链接
    print("Getting results page...")
    results_html = await fetch(f"{base_url}/results")
    if not results_html:
        return

    soup = BeautifulSoup(results_html, "lxml")
    
    # 找第一个 result
    result_con = soup.find("div", class_="result-con")
    if not result_con:
        print("No results found on page.")
        return

    link_tag = result_con.find("a", class_="a-reset")
    if not link_tag:
        print("No link found in result.")
        return
        
    match_url = base_url + link_tag['href']
    print(f"Analyzing match: {match_url}")
    
    # 2. 获取比赛详情页
    html = await fetch(match_url)
    if not html:
        return
        
    soup = BeautifulSoup(html, "lxml")
    
    # 3. 分析 Map Holders (寻找 Map Stats ID)
    print("\n" + "="*50)
    print("MAP HOLDERS ANALYSIS")
    print("="*50)
    
    map_holders = soup.find_all("div", class_="mapholder")
    for i, mh in enumerate(map_holders):
        print(f"\n[MapHolder {i}]")
        # 打印 map name
        name = mh.find("div", class_="mapname")
        print(f"  Name: {name.get_text(strip=True) if name else 'None'}")
        
        # 打印所有链接
        links = mh.find_all("a")
        for a in links:
            print(f"  Link: {a.get('href')}")
            
        # 打印 div 的属性
        print(f"  Attrs: {mh.attrs}")
        
        # 查找任何包含数字的 data 属性或 onclick
        results_center = mh.find("div", class_="results-center")
        if results_center:
             print(f"  Results Center Attrs: {results_center.attrs}")
             stats_link = results_center.find("div", class_="results-stats")
             if stats_link:
                 print(f"  Results Stats Attrs: {stats_link.attrs}")
    
    # 4. 分析 Stats Content Divs
    print("\n" + "="*50)
    print("STATS CONTENT DIVS")
    print("="*50)
    
    content_divs = soup.find_all("div", class_=lambda x: x and ('stats-content' in x or 'content' in x))
    # 过滤掉非相关的
    content_divs = [d for d in content_divs if d.get('id') and ('content' in d.get('id') or 'stats' in d.get('id'))]
    
    for div in content_divs:
        div_id = div.get("id")
        if not div_id or "match" not in div.get("class", []) and "stats-content" not in div.get("class", []):
             continue
             
        print(f"\n[Div ID: {div_id}] Classes: {div.get('class')}")
        tables = div.find_all("table", class_="totalstats")
        print(f"  Totalstats tables found: {len(tables)}")
        
        if tables:
            # 打印表头以确认列
            header_row = tables[0].find("tr", class_="header-row")
            if not header_row: header_row = tables[0].find("tr")
            if header_row:
                headers = [th.get_text(strip=True) for th in header_row.find_all(["th", "td"])]
                print(f"  Headers: {headers}")

if __name__ == "__main__":
    asyncio.run(main())
