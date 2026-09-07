# MoEngage Autonomous AI Agent (Gemini Powered)

A local autonomous growth marketing agent with a modern web dashboard. It authenticates into MoEngage using your active browser session cookies, leverages Google Gemini as the analytical & generative brain, and executes a daily automated process to discover revenue leaks, synthesize high-converting campaign ideas, and generate targeted audience segments.

---

## 🚀 Key Capabilities

1. **MoEngage Session Cookie Authentication**:
   - Zero complex OAuth setup. Simply paste your browser session cookie string from Chrome/Firefox/Safari DevTools.
   - Built-in session health validator and support for all MoEngage regional data centers (`dashboard-01`, `dashboard-02`, `dashboard-03`, `app.moengage.com`).
2. **Automated Daily Marketing Intelligence**:
   - Analyzes recent campaign metrics (delivery rates, CTR, conversions, revenue) and customer behavioral events.
   - Detects conversion drop-offs (e.g., Cart-to-Checkout drop-off rates).
   - Generates executive summaries, top 3 data-backed insights, ready-to-use campaign ideas, and high-impact micro-segments.
3. **Gemini Generative Engine**:
   - Crafts ready-to-deploy push notifications, emails, and in-app messages with personalized tokens (`{{UserAttribute['First Name']}}`), emojis, and urgency CTAs.
   - Proposes exact JSON filter criteria for MoEngage segments.
4. **Interactive AI Marketing Copilot (Chat)**:
   - Natural language interface to ask questions about your MoEngage data, audit past campaigns, brainstorm seasonal campaigns, or diagnose retention issues.
5. **1-Click Segment Deployment**:
   - Push recommended segments directly to your MoEngage workspace.
6. **Demo / Mock Mode**:
   - Test offline immediately with realistic simulated e-commerce campaign and segment data before connecting live credentials.

---

## ⚡️ Quick Start

### 1. Launch the Application
In your terminal, navigate to the directory and run:

```bash
cd /Users/kushagra.singh/.gemini/antigravity/scratch/moengage-agent
./start.sh
```
*(Or run `python3 start.py`)*

The script automatically sets up the Python virtual environment, installs dependencies, initializes the local SQLite database, and opens the server at:
👉 **`http://localhost:8080`**

---

## 🔑 Configuration Guide

### 1. Extracting MoEngage Session Cookies (30 Seconds)
1. Open your browser and log in to your MoEngage dashboard (e.g., `https://dashboard-01.moengage.com`).
2. Press **`F12`** (or **`Cmd + Option + I`** on Mac) to open Developer Tools.
3. Click the **Network** tab.
4. Filter by `Fetch/XHR` and click on any recent request (e.g. `segments`, `app/info`, or `campaigns`).
5. In the **Headers** panel, scroll to **Request Headers**.
6. Right-click the **`Cookie:`** line and select **Copy value**.
7. Navigate to the **Session & Settings** tab in the local agent UI (`http://localhost:8080`), paste the cookie string, and click **Test Connection**.

### 2. Google Gemini API Key
1. Get a free API key from [Google AI Studio](https://aistudio.google.com/app/apikey).
2. Enter the key in the **Session & Settings** tab of the local agent UI.
3. Select your desired model (`gemini-2.5-flash` recommended for speed, `gemini-1.5-pro` for deep reasoning).

---

## 📁 Project Structure

```
moengage-agent/
├── backend/
│   ├── main.py              # FastAPI server, REST routes & static file mounting
│   ├── database.py          # SQLite persistence (runs, segments, campaigns, chat)
│   ├── moengage_client.py   # Session cookie client & realistic mock data engine
│   ├── gemini_brain.py      # Gemini LLM brain & daily intelligence synthesizer
│   ├── scheduler.py         # Daily automation background runner (APScheduler)
│   └── tools.py             # Agent tools for function calling
├── frontend/
│   ├── index.html           # Modern responsive dashboard UI (Tailwind CSS)
│   ├── app.js               # Reactive SPA controller (Tabs, Chat, Charts, Modals)
│   └── style.css            # Custom styling, scrollbars & markdown typography
├── data/
│   └── agent.db             # Local SQLite database
├── requirements.txt         # Python dependencies
├── start.py                 # Cross-platform Python launcher
└── start.sh                 # 1-click startup script
```

---

## ⚙️ REST API Endpoints

- `GET /api/status`: System health, MoEngage connection status, and Gemini model status.
- `GET /api/automation/latest`: Retrieve the latest daily intelligence report, campaign ideas, and segments.
- `POST /api/automation/run`: Trigger the daily intelligence run on-demand.
- `POST /api/agent/chat`: Chat with the Gemini copilot about MoEngage data.
- `GET /api/moengage/campaigns`: List campaigns with status, CTR, and conversion metrics.
- `GET /api/moengage/segments`: List customer segments and filter criteria.
- `POST /api/moengage/segments/create`: Push a new filter segment to MoEngage.
- `POST /api/auth/test`: Test validity of current session cookies.
