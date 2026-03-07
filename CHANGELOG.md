# Changelog

## 0.1.9
- Search each item with its own vendors URL
- Fixed wrong login detection on Vendors page
- Request page 2 only if page 1 shows that page 2 exists
- Telegram alert now links to the correct item search page

## 0.1.8
- Added parsing with BeautifulSoup
- Added better text normalization for item names
- Added output of the lowest ad on the market
- Added merchant, shop and position to the output
- Script now shows lowest market price even if it is above the set limit

## 0.1.7
- Added user input for several item names and limits
- Script now accepts item/limit pairs from terminal
- Added separate tracking per item

## 0.1.6
- Added page status check
- Added debug output for login, cloudflare, recaptcha and unknown page
- Added page title output for easier checks

## 0.1.5
- Switched to Playwright browser session
- Added support for saved browser login state
- Added watcher based on browser session instead of plain requests

## 0.1.4
- Added more cookie/session handling fixes
- Added better request/session logic
- Added checks for expired or rejected session

## 0.1.3
- Set polling to 5 minutes
- Polling is now used as session keep-alive
- Added session expiry handling

## 0.1.2
- Added cookie input on script launch
- Cookie is entered by user instead of hardcoded value
- Added visible/hidden cookie prompt options during tests

## 0.1.1
- First working version of UARO market watcher
- Added price check for item from Vendors page
- Added Telegram notification when price is below limit
