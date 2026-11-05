import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App.jsx";
import { LightboxProvider } from "./components/Lightbox.jsx";
import "./styles.css";

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <LightboxProvider>
      <App />
    </LightboxProvider>
  </React.StrictMode>,
);
