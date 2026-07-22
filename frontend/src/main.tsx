import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import "./styles.css";

// Profiles and tailoring results change only when this user changes them, so
// nothing is refetched behind their back while they are editing. The defaults
// have to say that, not just the comment: with `staleTime: 0` every new
// observer of ["profile", id] refetched — and there are two, ProfilePanel and
// TailorPanel — while `refetchOnReconnect` refired the query on any
// online/offline flicker and `retry: false` turned the first blip into an
// error state. The save path invalidates explicitly, so freshness does not
// depend on background refetching.
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      refetchOnWindowFocus: false,
      refetchOnReconnect: false,
      retry: 1,
    },
  },
});

createRoot(document.getElementById("root") as HTMLElement).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
);
