# Atom Registry web app

React + TypeScript (Vite) with TanStack Query. In development, Vite proxies `/api`, `/internal`
and `/docs` to the FastAPI backend (`ATOM_API_URL`, default `http://localhost:8000`).

```bash
npm install
npm run dev      # http://localhost:5173
npm run build    # type check + production build
```
