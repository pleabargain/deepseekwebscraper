import os
import asyncio
import json
import logging
import argparse
import re
from datetime import datetime, timezone
from pydantic import BaseModel, Field
from typing import List
from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode
from crawl4ai.extraction_strategy import LLMExtractionStrategy

def validate_url(url: str) -> str:
    """Validate URL format and provide helpful error messages."""
    valid_protocols = ['http://', 'https://', 'file://', 'raw:']
    
    # Check for double protocol errors (e.g., "https://https://")
    for protocol in valid_protocols:
        if url.count(protocol) > 1:
            fixed_url = url.replace(f"{protocol}{protocol}", protocol)
            raise argparse.ArgumentTypeError(
                f"\nInvalid URL: Found duplicate protocol '{protocol}'\n"
                f"Current: {url}\n"
                f"Try this instead: {fixed_url}\n\n"
                f"Common URL formats:\n"
                f"  ✓ https://example.com\n"
                f"  ✓ http://blog.example.com\n"
                f"  ✓ file:///C:/path/to/file.html\n"
                f"  ✗ https://https://example.com (duplicate protocol)\n"
                f"  ✗ example.com (missing protocol)\n"
                f"  ✗ www.example.com (missing protocol)"
            )
    
    # Check if URL starts with any valid protocol
    if not any(url.startswith(protocol) for protocol in valid_protocols):
        protocols_str = "', '".join(valid_protocols)
        raise argparse.ArgumentTypeError(
            f"\nInvalid URL format: '{url}'\n"
            f"URL must start with one of: '{protocols_str}'\n"
            f"Examples:\n"
            f"  ✓ https://example.com\n"
            f"  ✓ http://blog.example.com\n"
            f"  ✓ file:///C:/path/to/file.html\n"
            f"  ✗ example.com (missing protocol)\n"
            f"  ✗ www.example.com (missing protocol)"
        )
    
    return url

# Configure logging
log_dir = "logs"
if not os.path.exists(log_dir):
    os.makedirs(log_dir)

logging.basicConfig(
    level=logging.ERROR,
    format='[%(asctime)s] [%(levelname)s] %(message)s\nStack trace:\n%(exc_info)s\n---\n',
    handlers=[
        logging.FileHandler(os.path.join(log_dir, "error.log"), mode='a'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class ExtractedItem(BaseModel):
    title: str
    date: str | None = None

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "title": "Sample Blog Post",
                    "date": "2025-01-01"
                }
            ]
        }
    }

class ScrapedContent(BaseModel):
    url: str
    instruction: str
    timestamp: datetime
    results: List[ExtractedItem]

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "url": "https://example.com",
                    "instruction": "Extract blog posts with title and date",
                    "timestamp": "2025-02-08T11:22:41Z",
                    "results": [
                        {
                            "title": "Sample Blog Post",
                            "date": "2025-01-01"
                        }
                    ]
                }
            ]
        }
    }

def parse_args():
    parser = argparse.ArgumentParser(
        description='''
DeepSeek Web Scraper - Extract content using LLM-powered analysis

This tool scrapes content from websites based on custom instructions and extracts structured data
using the Ollama deepseek model. It requires a URL to scrape, extraction instruction, and
temperature setting for the LLM, with additional optional parameters for fine-tuning the extraction.
''',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  # Basic usage with required parameters
  python app.py --url "https://example.com" --instruction "Extract blog posts with title and date" --temp 0.0

  # Advanced usage with optional parameters
  python app.py --url "https://example.com" --instruction "Find news articles and extract their titles and dates" \\
                --temp 0.0 --chunk-size 2000 --output results.json

  # Full configuration
  python app.py --url "https://example.com" --instruction "Extract product reviews with title and date" \\
                --temp 0.0 --chunk-size 2000 --overlap 0.1 --max-tokens 1000 --headless false \\
                --format html --output results.json --model deepseek-r1
        '''
    )

    required = parser.add_argument_group('Required Arguments')
    required.add_argument('--url', type=validate_url, required=True,
                         help='Website URL to scrape (must start with http://, https://, file://, or raw:)')
    required.add_argument('--instruction', type=str, required=True,
                         help='Extraction instruction (e.g., "Extract blog posts with title and date")')
    required.add_argument('--temp', type=float, required=True,
                         help='Temperature for LLM (0.0 to 1.0)')

    optional = parser.add_argument_group('Optional Arguments')
    optional.add_argument('--chunk-size', type=int, default=1000,
                         help='Token threshold for chunking (default: 1000)')
    optional.add_argument('--overlap', type=float, default=0.0,
                         help='Overlap rate between chunks (default: 0.0)')
    optional.add_argument('--max-tokens', type=int, default=800,
                         help='Maximum tokens for LLM response (default: 800)')
    optional.add_argument('--headless', type=str, default='true',
                         choices=['true', 'false'],
                         help='Run browser in headless mode (default: true)')
    optional.add_argument('--format', type=str, default='markdown',
                         choices=['markdown', 'html', 'text'],
                         help='Input format (default: markdown)')
    optional.add_argument('--output', type=str,
                         help='Output file path for JSON results')
    optional.add_argument('--model', type=str, default='deepseek-r1',
                         help='LLM model to use (default: deepseek-r1)')

    return parser.parse_args()
    
async def main():
    try:
        args = parse_args()
        
        llm_strategy = LLMExtractionStrategy(
            provider=f"ollama/{args.model}:latest", 
            api_token="none",
            schema=json.dumps(ExtractedItem.model_json_schema()),
            extraction_type="schema",
            instruction=args.instruction,
            chunk_token_threshold=args.chunk_size,
            overlap_rate=args.overlap,
            apply_chunking=True,
            input_format=args.format,
            extra_args={
                "temperature": args.temp,
                "max_tokens": args.max_tokens
            }
        )

        crawl_config = CrawlerRunConfig(
            extraction_strategy=llm_strategy,
            cache_mode=CacheMode.BYPASS
        )

        browser_cfg = BrowserConfig(
            headless=args.headless.lower() == 'true'
        )

        async with AsyncWebCrawler(config=browser_cfg) as crawler:
            result = await crawler.arun(
                url=args.url,
                config=crawl_config
            )

            if result.success:
                extracted_items = [
                    ExtractedItem(**item)
                    for item in json.loads(result.extracted_content)
                ]
                
                # Create full content object
                content = ScrapedContent(
                    url=args.url,
                    instruction=args.instruction,
                    timestamp=datetime.now(timezone.utc),
                    results=extracted_items
                )
                
                # Generate timestamped filename with URL and instruction hash
                timestamp = content.timestamp.strftime("%Y%m%d_%H%M%S")
                url_part = re.sub(r'[^\w]+', '_', args.url.split('://')[-1])[:50]
                instruction_hash = hex(hash(args.instruction) & 0xFFFFFF)[2:]  # Last 6 chars of hash
                filename = f"{url_part}_{instruction_hash}_{timestamp}.json"
                
                # Save to file
                with open(filename, 'w') as f:
                    json.dump(content.model_dump(mode='json'), f, indent=2)
                print(f"Results saved to {filename}")
                
                # Also print to console if output is small
                if len(content.results) < 5:
                    print("\nExtracted content:", json.dumps(content.model_dump(mode='json'), indent=2))
                llm_strategy.show_usage()
            else:
                logger.error(f"Extraction failed: {result.error_message}")
                print("Error:", result.error_message)
    except Exception as e:
        logger.error("An error occurred during execution", exc_info=True)
        raise

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except SystemExit as e:
        # Catch argument parsing errors and log them
        if e.code != 0:  # Non-zero exit means error
            error_msg = "Missing required arguments. Please provide --url (with protocol), --instruction, and --temp parameters."
            logger.error(f"{error_msg}\nRun 'python app.py --help' for usage guide.")
            print(f"\nError: {error_msg}")
            print("Examples:")
            print("  python app.py --url \"https://example.com\" --instruction \"Extract blog posts with title and date\" --temp 0.0")
            print("    → Saves to: example_com_a1b2c3_20250208_110209.json")
            print("  python app.py --url \"http://blog.example.com\" --instruction \"Extract news articles with title and date\" --temp 0.0")
            print("    → Saves to: blog_example_com_d4e5f6_20250208_110209.json")
    except Exception as e:
        logger.error("Fatal error in main execution", exc_info=True)
        raise
