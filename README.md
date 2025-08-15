# Alabama DOC Inmate Scraper

A high-performance scraper built for an Upwork job that was awarded to someone else...

![Python Version](https://img.shields.io/badge/python-3.8+-blue.svg)
![Dependencies](https://img.shields.io/badge/dependencies-pandas%2C%20bs4%2C%20requests-brightgreen)
![License](https://img.shields.io/badge/license-MIT-green.svg)

---

## Story

I came across an Upwork job offering for a freelancer to scrape all inmate information from the Alabama Department of Corrections website. Rather than just submitting a proposal with vague promises, I decided to solve the entire problem upfront.

I built and ran this scraper and generated the complete dataset the client requested. I attached the final Excel file to my proposal, effectively delivering the finished product before even being hired.

The client ended up hiring someone else (perhaps they never saw my application). So I open-sourced it.

### Performance Comparison
up
The key to this approach was bypassing slow browser automation (like Selenium or Playwright) and instead reverse-engineering the website's underlying ASP.NET HTTP requests. This resulted in a massive performance gain.

| Method | Estimated Time to Complete | Actual Time to Complete |
| :--- | :--- | :--- |
| **Browser Automation** (e.g., Playwright) | `~ 15+ Hours` | N/A |
| **Direct HTTP Requests (This Repo)** | `~ 30 Minutes` | **< 30 Minutes** |

The initial collection of all 27,000+ unique inmate ID numbers (`AIS #`) completes in about **10 seconds**.

## How It Works: The Two-Phase Approach

The scraper is designed in two phases.

### Phase 1: `collect_ais.py` - ID Number Collection

This script is responsible for finding every unique inmate AIS number on the site.

1.  It iterates through each letter of the alphabet (`a` to `z`) as a search term for the "Last Name" field.
2.  For each letter, it launches a parallel worker thread (up to 26 workers).
3.  Each worker performs the initial search and then rapidly paginates through all result pages, collecting every AIS number it finds.
4.  All unique AIS numbers are consolidated and saved to a checkpoint file: `ais_numbers_checkpoint.txt`.

This phase is fast, typically finishing in **under 15 seconds**.

### Phase 2: `process_ais.py` -  Inmate Data Scraping

This script reads the AIS numbers from the checkpoint file and scrapes the detailed profile for each one.

1.  It loads the list of AIS numbers to be processed.
2.  It checks for an existing output CSV and resumes from where it left off.
3.  It uses a `ThreadPoolExecutor` to process up to 50 inmates concurrently.
4.  For each AIS number, a worker sends a direct POST request to fetch the inmate's details page.
5.  The HTML is parsed with BeautifulSoup to extract all relevant information, including demographics, incarceration history, sentences, aliases, and tattoos.
6.  The data is appended directly to the final CSV file, `alabama_inmates_database.csv`.

## Reverse-Engineering ASP.NET

The Alabama DOC website is built on ASP.NET, which uses a "ViewState" mechanism to maintain state between requests. A browser normally handles this automatically, but to do it with `requests`, I had to mimic that behavior.

Here was the process:

1.  **Inspect Network Traffic:** Using browser developer tools, I monitored the network requests made when performing a search.
2.  **Identify Key Payloads:** I discovered that any POST request (like a search or clicking "next page") required several hidden form inputs to be sent back to the server. These are:
    *   `__VIEWSTATE`
    *   `__VIEWSTATEGENERATOR`
    *   `__EVENTVALIDATION`
3.  **Mimic the Browser Flow:** The scripts replicate the browser's actions:
    *   First, send a GET request to the search page to get an initial, valid set of `__VIEWSTATE` tokens.
    *   Extract these values from the HTML using BeautifulSoup.
    *   Construct a `data` payload for a POST request that includes these tokens along with our search term (e.g., `ctl00$MainContent$txtLName: 'a'`).
    *   For pagination or clicking on a record, the process is the same, but we also include an `__EVENTTARGET` value that tells the server which button was "clicked".

This direct-request method is orders of magnitude faster and uses significantly fewer resources than loading a full browser for each request.

## Setup and Usage

### Prerequisites
*   Python 3.13
*   `pip` for installing packages

### 1. Clone the Repo

```bash
git clone https://github.com/vekoada/alabama_inmates.git
cd alabama_inmates
```

### 2. Set Up VENV 

```bash
# For Unix/macOS
python3 -m venv venv
source venv/bin/activate

# For Windows
python -m venv venv
.\venv\Scripts\activate
```

### 3. Install Dependencies

```bash
pip install pandas beautifulsoup4 requests lxml
```

### 4. Run the Scraper

The process is a simple two-step execution.

**Step 1: Collect all inmate AIS numbers.**
```bash
python collect_ais.py
```
This will create `ais_numbers_checkpoint.txt`.

**Step 2: Process the AIS numbers to get inmate details.**
```bash
python process_ais.py
```
This will read the checkpoint file and create `alabama_inmates_database_2.csv`. You can run this command again to resume if it gets interrupted.

## Output Files

*   `ais_numbers_checkpoint.txt`: A text file containing one unique inmate AIS number per line.
*   `alabama_inmates_database.csv`: The final dataset containing all scraped information.



## License

This project is licensed under the MIT License. 
