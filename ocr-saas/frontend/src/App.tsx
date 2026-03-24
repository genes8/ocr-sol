import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "react-hot-toast";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { Layout } from "./components/layout/main";
import { Dashboard } from "./pages/dashboard";
import { DocumentDetail } from "./pages/document-detail";
import { Documents } from "./pages/documents";
import { Login } from "./pages/login";
import { Review } from "./pages/review";
import { Settings } from "./pages/settings";
import { Upload } from "./pages/upload";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      staleTime: 30000,
    },
  },
});

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route element={<Layout />}>
            <Route path="/" element={<Dashboard />} />
            <Route path="/documents" element={<Documents />} />
            <Route path="/documents/:id" element={<DocumentDetail />} />
            <Route path="/upload" element={<Upload />} />
            <Route path="/review" element={<Review />} />
            <Route path="/review/:id" element={<Review />} />
            <Route path="/settings" element={<Settings />} />
          </Route>
        </Routes>
      </BrowserRouter>
      <Toaster
        position="top-right"
        toastOptions={{
          duration: 4000,
          style: {
            background: "#363636",
            color: "#fff",
          },
        }}
      />
    </QueryClientProvider>
  );
}

export default App;
