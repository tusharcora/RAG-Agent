# Provider logos

The app shows the providers' official logos, which this repo does not ship. Download them from each company's
own brand page and save them here (SVG preferred; PNG works with the same base name):

| File | Source |
| --- | --- |
| `google.svg` | https://developers.google.com/identity/branding-guidelines |
| `github.svg` | https://github.com/logos |
| `notion.svg` | Notion's official brand / media kit page |
| `jira.svg` | https://atlassian.design/resources/logo-library |

Where they appear:

- **Sign-in confirmation page** (the browser tab after Connect or sign-in, `services/api/app/api/connect_page.py`):
  picked up automatically once the file is here and the UI is rebuilt. Without a file, the provider's name stands in.
- **Google / GitHub sign-in buttons:** also need `NEXT_PUBLIC_BRAND_MARKS=1` when building the frontend; without it
  the buttons are text-only.
