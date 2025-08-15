import requests
import pandas as pd
from bs4 import BeautifulSoup, Tag
import os
import time
import csv
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Set, Tuple, Dict, Optional


CHECKPOINT_FILE = "ais_numbers_checkpoint.txt"
OUTPUT_CSV_FILE = "alabama_inmates_database_2.csv"
BASE_URL = "https://doc.alabama.gov/inmatesearch.aspx"
DETAILS_URL = "https://doc.alabama.gov/InmateInfo.aspx"
MAX_WORKERS = 50

def parse_hidden_inputs(soup):
    """Extracts the necessary ASP.NET form values from a BeautifulSoup object."""
    viewstate = soup.find('input', {'name': '__VIEWSTATE'})
    viewstategen = soup.find('input', {'name': '__VIEWSTATEGENERATOR'})
    eventvalidation = soup.find('input', {'name': '__EVENTVALIDATION'})
    
    if not all([viewstate, viewstategen, eventvalidation]):
        raise ValueError("A required ASP.NET hidden input was not found on the page.")
        
    return {
        '__VIEWSTATE': viewstate['value'],
        '__VIEWSTATEGENERATOR': viewstategen['value'],
        '__EVENTVALIDATION': eventvalidation['value']
    }


# If the website changes an ID, we only have to update it in one place.
_INMATE_SUMMARY_TABLE_ID = 'MainContent_DetailsView2'
_DEMOGRAPHICS_TABLE_ID = 'MainContent_DetailsView1'
_INCARCERATION_TABLE_ID = 'MainContent_gvSentence'
_INCARCERATION_DETAIL_TABLE_ID_TPL = 'MainContent_gvSentence_GridView1_{index}'
_SECTIONS_TO_PARSE = ['Aliases', 'Scars, Marks and Tattoos']

def _parse_inmate_summary(soup: BeautifulSoup, fallback_ais_num: str) -> Dict[str, str]:
    summary_data = {}
    table = soup.find('table', id=_INMATE_SUMMARY_TABLE_ID)
    if not table:
        return {'AIS #': fallback_ais_num} # Return fallback if table is missing

    rows = table.find_all('tr')
    
    if len(rows) > 0 and (name_tag := rows[0].find('span')):
        summary_data['Inmate Name'] = name_tag.get_text(strip=True)
    if len(rows) > 1 and (ais_tag := rows[1].find('span')):
        summary_data['AIS #'] = ais_tag.get_text(strip=True)
    else:
        summary_data['AIS #'] = fallback_ais_num
    if len(rows) > 3 and (inst_tag := rows[3].find('span')):
        summary_data['Institution'] = inst_tag.get_text(strip=True)
        
    return summary_data


def _parse_demographics(soup: BeautifulSoup) -> Dict[str, str]:
    demographics_data = {}
    table = soup.find('table', id=_DEMOGRAPHICS_TABLE_ID)
    if not table:
        return {}

    for row in table.find_all('tr'):
        cells = row.find_all('td')
        if len(cells) == 2:
            key = cells[0].get_text(strip=True).replace(':', '')
            value = cells[1].get_text(strip=True)
            if key: # Ensure we don't add empty keys
                demographics_data[key] = value
                
    return demographics_data


def _parse_text_sections(soup: BeautifulSoup) -> Dict[str, str]:
    section_data = {}
    for item_name in _SECTIONS_TO_PARSE:
        key_name = item_name.replace(', ', '_')
        
        header = soup.find(lambda tag: tag.name == 'div' and f'{item_name}:' in tag.get_text())
        
        if header:
            items = [span.get_text(strip=True) for span in header.find_next_siblings('span')]
            # Check for the "No known..." placeholder text
            if items and f"No known {item_name}" not in items[0]:
                section_data[key_name] = ' || '.join(items)
            else:
                section_data[key_name] = '' 
        else:
             section_data[key_name] = '' 

    return section_data


def _parse_incarceration_history(soup: BeautifulSoup) -> List[Dict[str, str]]:
    all_sentence_details = []
    incarceration_summary_tables = soup.find_all('table', id=_INCARCERATION_TABLE_ID)

    for i, summary_table in enumerate(incarceration_summary_tables):
        summary_rows = summary_table.find_all('tr')
        if len(summary_rows) < 2:
            continue
        
        summary_headers = [th.get_text(strip=True) for th in summary_rows[0].find_all('td')]
        summary_values = [td.get_text(strip=True) for td in summary_rows[1].find_all('td')]
        summary_info = {f"Incarceration {h}": v for h, v in zip(summary_headers, summary_values)}

        # Find the corresponding detail table for the sentences
        nested_table_id = _INCARCERATION_DETAIL_TABLE_ID_TPL.format(index=i)
        nested_table = soup.find('table', id=nested_table_id)
        if not nested_table:
            continue
        
        # Extract headers and rows from the detail table
        nested_header_tags = nested_table.find('tr').find_all('th')
        nested_headers = ["Sentence " + th.get_text(strip=True) for th in nested_header_tags]
        
        for sentence_row in nested_table.find_all('tr')[1:]:
            cells = sentence_row.find_all('td')
            sentence_data = {nested_headers[c_idx]: cell.get_text(strip=True) for c_idx, cell in enumerate(cells)}
            
            # Combine the overarching summary info with specific sentence data
            full_sentence_record = {**summary_info, **sentence_data}
            all_sentence_details.append(full_sentence_record)

    return all_sentence_details

def parse_final_details_page(soup: BeautifulSoup, ais_num: str) -> List[Dict[str, str]]:
    base_inmate_data = {}
    base_inmate_data.update(_parse_inmate_summary(soup, ais_num))
    base_inmate_data.update(_parse_demographics(soup))
    base_inmate_data.update(_parse_text_sections(soup))

    all_sentence_details = _parse_incarceration_history(soup)

    if not all_sentence_details:
        return [base_inmate_data]
    
    final_records = []
    for sentence in all_sentence_details:
        full_record = base_inmate_data.copy() 
        full_record.update(sentence)         
        final_records.append(full_record)
        
    return final_records

def _create_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/98.0.4758.102 Safari/537.36"
    })
    return session

def _get_initial_search_page(session: requests.Session):
    response = session.get(BASE_URL, timeout=30)
    response.raise_for_status()
    return BeautifulSoup(response.text, 'lxml')

def _post_ais_search(session: requests.Session, initial_soup: BeautifulSoup, ais_num: str):
    payload = parse_hidden_inputs(initial_soup)
    payload.update({
        'ctl00$MainContent$txtAIS': ais_num,
        'ctl00$MainContent$btnSearch': 'Search'
    })
    response = session.post(BASE_URL, data=payload, allow_redirects=True, timeout=30)
    response.raise_for_status()
    return BeautifulSoup(response.text, 'lxml')

def _navigate_to_details_page(session: requests.Session, results_soup: BeautifulSoup):
    link = results_soup.select_one("#MainContent_gvInmateResults a[id*='lnkInmateName']")
    if not link:
        return None

    payload = parse_hidden_inputs(results_soup)
    try:
        event_target = link['href'].split("'")[1]
        payload['__EVENTTARGET'] = event_target
    except (IndexError, KeyError):
        return None

    response = session.post(DETAILS_URL, data=payload, allow_redirects=True, timeout=30)
    response.raise_for_status()
    return BeautifulSoup(response.text, 'lxml')

def process_single_ais(ais_num: str):
    try:
        with _create_session() as session:
            initial_soup = _get_initial_search_page(session)
            
            results_soup = _post_ais_search(session, initial_soup, ais_num)
            
            final_soup = _navigate_to_details_page(session, results_soup)

            if not final_soup:
                return [{'AIS #': ais_num, 'Status': 'No_Result_Found'}]

            return parse_final_details_page(final_soup, ais_num)

    except Exception as e:
        return [{'AIS #': ais_num, 'Status': f'Error: {type(e).__name__} - {e}'}]

def load_target_ais_numbers(filepath: str) -> List[str]:
    if not os.path.exists(filepath):
        # Raise a specific, expected error that the caller can handle.
        raise FileNotFoundError(
            f"Checkpoint file '{filepath}' not found. "
            "Please run the Phase 1 collector script first."
        )
    with open(filepath, 'r') as f:
        return [line.strip() for line in f if line.strip()]

def load_processed_ais_set(filepath: str) -> Tuple[Set[str], bool]:
    if not os.path.exists(filepath):
        return set(), False

    print(f"--- Output file '{filepath}' found. Checking for completed items. ---")
    try:
        df_existing = pd.read_csv(filepath, usecols=['AIS #'], low_memory=False, dtype=str)
        processed_set = set(df_existing['AIS #'])
        print(f"--- Found {len(processed_set)} inmates already processed. Resuming. ---")
        return processed_set, True
    except (pd.errors.EmptyDataError, KeyError, FileNotFoundError):
        print("--- Output file is empty or invalid. Starting from scratch. ---")
        return set(), False
    except Exception as e:
        print(f"--- Could not read existing CSV due to an error: {e}. Starting fresh. ---")
        return set(), False

def _print_progress(processed: int, total: int, start_time: float):
    elapsed_time = time.time() - start_time
    rate = processed / elapsed_time if elapsed_time > 0 else 0
    remaining = total - processed
    eta_seconds = remaining / rate if rate > 0 else 0
    eta_str = time.strftime("%H:%M:%S", time.gmtime(eta_seconds)) if eta_seconds > 0 else "N/A"

    print(
        f"Progress: {processed}/{total} ({processed/total:.1%}) | "
        f"Rate: {rate:.2f} items/sec | ETA: {eta_str}",
        end='\r'
    )

def process_and_write_data(numbers_to_process: List[str], output_file: str, is_resuming: bool):
    print(f"--- Starting data collection with {MAX_WORKERS} parallel workers. ---")
    
    # Open file in 'append' mode if resuming, 'write' mode if new.
    file_mode = 'a' if is_resuming else 'w'
    with open(output_file, file_mode, newline='', encoding='utf-8') as f:
        writer = None
        processed_count = 0
        total_to_process = len(numbers_to_process)
        start_time = time.time()

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            future_to_ais = {executor.submit(process_single_ais, num): num for num in numbers_to_process}

            for future in as_completed(future_to_ais):
                result_records = future.result()
                processed_count += 1

                if result_records:
                    # Lazily initialize the CSV writer with headers from the first valid result.
                    if writer is None:
                        all_keys = set().union(*(d.keys() for d in result_records))
                        headers = sorted(list(all_keys))
                        writer = csv.DictWriter(f, fieldnames=headers, extrasaction='ignore')
                        if not is_resuming:
                            writer.writeheader()

                    writer.writerows(result_records)
                    f.flush()  # Force write to disk to save progress.

                _print_progress(processed_count, total_to_process, start_time)

    print(f"\n--- Data collection complete. All data saved to '{output_file}'. ---")

def main():
    try:
        all_ais_numbers = load_target_ais_numbers(CHECKPOINT_FILE)
    except FileNotFoundError as e:
        print(f"Error: {e}")
        return

    processed_ais_set, is_resuming = load_processed_ais_set(OUTPUT_CSV_FILE)

    numbers_to_process = [num for num in all_ais_numbers if num not in processed_ais_set]
    
    if not numbers_to_process:
        print("\n--- All AIS numbers have already been processed. The file is up to date. ---")
        return

    print(f"--- Total to process: {len(all_ais_numbers)} | Already done: {len(processed_ais_set)} | Remaining: {len(numbers_to_process)} ---")
    
    process_and_write_data(numbers_to_process, OUTPUT_CSV_FILE, is_resuming)


if __name__ == "__main__":
    main()