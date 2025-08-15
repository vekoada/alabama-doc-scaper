import requests
from bs4 import BeautifulSoup
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import sys

SEARCH_TERMS = list('abcdefghijklmnopqrstuvwxyz')
CHECKPOINT_FILE = "ais_numbers_checkpoint.txt"
BASE_URL = "https://doc.alabama.gov/inmatesearch.aspx"
MAX_WORKERS = 26

def parse_hidden_inputs(soup):
    """Extracts the necessary ASP.NET form values from a BeautifulSoup object."""
    return {
        '__VIEWSTATE': soup.find('input', {'name': '__VIEWSTATE'})['value'],
        '__VIEWSTATEGENERATOR': soup.find('input', {'name': '__VIEWSTATEGENERATOR'})['value'],
        '__EVENTVALIDATION': soup.find('input', {'name': '__EVENTVALIDATION'})['value']
    }

def _create_session():
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/98.0.4758.102 Safari/537.36"
    })
    return session

def _extract_form_payload(soup):
    payload = {}
    for hidden_input in soup.find_all('input', {'type': 'hidden'}):
        name = hidden_input.get('name')
        value = hidden_input.get('value', '')
        if name:
            payload[name] = value
    return payload

def _scrape_ais_numbers_from_page(soup):
    results_table = soup.find('table', id='MainContent_gvInmateResults')
    if not results_table:
        return set()

    found_numbers = set()
    for row in results_table.find_all('tr'):
        first_cell = row.find('td')
        if first_cell:
            ais_num = first_cell.get_text(strip=True)
            if ais_num.isdigit():
                found_numbers.add(ais_num)
    return found_numbers

def _get_next_page_target(soup):
    next_button = soup.find('input', {'name': lambda x: x and 'btnNext' in x})
    if not next_button or next_button.get('disabled'):
        return None
    return next_button['name']

def _perform_initial_search(session, term):
    initial_response = session.get(BASE_URL)
    initial_response.raise_for_status()
    soup = BeautifulSoup(initial_response.text, 'lxml')
    payload = _extract_form_payload(soup)

    payload.update({
        'ctl00$MainContent$txtLName': term,
        'ctl00$MainContent$btnSearch': 'Search'
    })
    response = session.post(BASE_URL, data=payload, allow_redirects=True)
    response.raise_for_status()
    return response

def _paginate_and_scrape(session, first_page_response, term):
    term_ais_numbers = set()
    response = first_page_response
    page_num = 1
    
    while True:
        progress_line = f"[Worker '{term}'] Scraping Page: {page_num:<4} | Numbers found for this term: {len(term_ais_numbers):<5}"
        print(f"\r{progress_line}", end="", flush=True)

        soup = BeautifulSoup(response.text, 'lxml')
        
        found_on_page = _scrape_ais_numbers_from_page(soup)
        newly_found_count = len(found_on_page - term_ais_numbers)
        term_ais_numbers.update(found_on_page)

        next_page_target = _get_next_page_target(soup)
        if not next_page_target:
            break
            
        if newly_found_count == 0 and page_num > 1:
            print(f"\r[Worker '{term}'] Stall detected on page {page_num}. No new numbers found. Ending term search. {' '*20}")
            break

        payload = _extract_form_payload(soup)
        payload['__EVENTTARGET'] = next_page_target
        
        response = session.post(response.url, data=payload, allow_redirects=True)
        response.raise_for_status()
        page_num += 1
        
    print(f"\r{' ' * len(progress_line)}\r", end="", flush=True)
    return term_ais_numbers


def collect_for_term(term):
    worker_start_time = time.time()
    try:
        with _create_session() as session:
            first_page_response = _perform_initial_search(session, term)
            all_ais_numbers = _paginate_and_scrape(session, first_page_response, term)
        
        duration = time.time() - worker_start_time
        return term, all_ais_numbers, duration

    except Exception as e:
        print(f"\r{' ' * 80}\r", end="", flush=True) 
        print(f"\n! [ERROR] Worker for term '{term}' failed !", file=sys.stderr)
        print(f"       Reason: {e}\n", file=sys.stderr)
        return term, set(), 0.0

def _print_header(total_terms, max_workers, url, outfile):
    print("=" * 70)
    print("Starting Alabama Inmate AIS Number Collection...")
    print(f"Target URL: {url}")
    print(f"Processing {total_terms} search terms: '{''.join(SEARCH_TERMS)}'")
    print(f"Using up to {max_workers} parallel workers.")
    print(f"Output will be saved to: {outfile}")
    print("=" * 70)

def _run_concurrent_collection(search_terms, max_workers):
    all_ais_numbers = set()
    total_terms = len(search_terms)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_term = {executor.submit(collect_for_term, term): term for term in search_terms}
        
        print("\nSubmitting all tasks to the thread pool. Waiting for results...\n")
        
        for i, future in enumerate(as_completed(future_to_term), 1):
            term, term_results, duration = future.result()
            
            status = "[SUCCESS]" if duration > 0 else "[FAILURE]"
            
            if term_results:
                all_ais_numbers.update(term_results)
            
            print(f"({i:>2}/{total_terms}) {status:<9} Term '{term}': "
                  f"Found {len(term_results):>5} numbers in {duration:5.1f}s. "
                  f"| Total Unique AIS: {len(all_ais_numbers)}")
                  
    return all_ais_numbers

def _save_results_to_file(numbers, filename):
    print("\n" + "=" * 70)
    print("Collection complete. Consolidating and saving results...")
    
    sorted_numbers = sorted(list(numbers))
    
    print(f"Writing {len(sorted_numbers)} unique numbers to '{filename}'...")
    with open(filename, 'w') as f:
        for num in sorted_numbers:
            f.write(f"{num}\n")
    print("...Save complete.")

def _print_summary(total_count, duration, filename):
    print("\n" + "=" * 70)
    print(">>> Phase 1 Finished! <<<")
    print(f"Collected a total of {total_count} unique AIS numbers.")
    print(f"Total time taken: {duration:.2f} seconds ({duration/60:.2f} minutes).")
    print(f"Checkpoint file '{filename}' is ready for Phase 2 processing.")
    print("=" * 70)

def main():
    """
    Orchestrates the concurrent collection of all AIS numbers.
    """
    start_time = time.time()
    
    _print_header(
        total_terms=len(SEARCH_TERMS), 
        max_workers=MAX_WORKERS, 
        url=BASE_URL, 
        outfile=CHECKPOINT_FILE
    )

    all_ais_numbers = _run_concurrent_collection(SEARCH_TERMS, MAX_WORKERS)

    _save_results_to_file(all_ais_numbers, CHECKPOINT_FILE)
            
    total_duration = time.time() - start_time
    
    _print_summary(
        total_count=len(all_ais_numbers), 
        duration=total_duration, 
        filename=CHECKPOINT_FILE
    )

if __name__ == "__main__":
    main()