import { useEffect, useState } from "react";
import { ValidationEntry } from "./data/types.ts";
import "./index.css";
import FileUpload from "./components/FileUpload";
import ValidationTable from "./components/ValidationTable";
import IaCResults from "./components/IaCResults.tsx";
import MicrosoftLogo from "./components/MicrosoftLogo.tsx";
import AiSearchBadge from "./components/AiSearchBadge.tsx";
import UserProfile from "./components/UserProfile.tsx";

export default function App() {
  const [validateResult, setValidateResult] = useState<
    ValidationEntry[] | null
  >(null);
  const [IaCResult, setIaCResult] = useState<string[] | null>(null)
  const [newestResult, setNewestResult] = useState<string | null>(null)

  useEffect(() => {
    if(newestResult) {
      document
        .getElementById(newestResult)!
        .scrollIntoView({ behavior: "smooth" });
    }
  }, [newestResult]);

  return (
    <div className="flex flex-col bg-ms-gray min-h-screen w-screen overflow-x-clip font-main">
      {/* Top Navigation Bar */}
      <header className="w-full bg-white border-b border-ms-border px-6 py-3 flex items-center justify-between shadow-sm">
        <div className="flex items-center gap-3">
          <MicrosoftLogo className="h-7" />
          <div className="flex flex-col">
            <span className="text-base font-semibold text-ms-text leading-tight">Architecture Review Board</span>
            <span className="text-[10px] text-ms-text-secondary leading-tight">MAS - Agentic Flow</span>
          </div>
        </div>
        <div className="flex items-center gap-4">
          <AiSearchBadge />
          <UserProfile />
        </div>
      </header>

      {/* Hero Section */}
      <div className="w-full bg-gradient-to-r from-ms-blue to-ms-accent py-10 px-6">
        <div className="max-w-4xl mx-auto text-center">
          <h1 className="text-white text-3xl font-bold tracking-tight">
            Architecture Design Validator / IaC Generator
          </h1>
          <p className="text-blue-100 text-sm mt-2 opacity-90">
            Upload an architecture document to validate against organizational policies and generate Infrastructure-as-Code
          </p>
        </div>
      </div>

      {/* Main Content */}
      <main className="flex flex-col items-center gap-y-6 py-8 px-4 max-w-7xl mx-auto w-full">
        <div className="bg-white rounded-xl shadow-sm border border-ms-border p-6 w-full max-w-2xl">
          <FileUpload setValidateResult={setValidateResult} setIaCResult={setIaCResult} setNewestResult={setNewestResult}/>
        </div>

        <div id="ValidationTable" className="w-full">
          {validateResult && (
            <div className="bg-white rounded-xl shadow-sm border border-ms-border p-6">
              <ValidationTable validateResult={validateResult} />
            </div>
          )}
        </div>

        <div id="IaCResults" className="w-full">
          {IaCResult && (
            <div className="bg-white rounded-xl shadow-sm border border-ms-border p-6">
              <IaCResults results={IaCResult} />
            </div>
          )}
        </div>
      </main>

      {/* Footer */}
      <footer className="mt-auto w-full bg-white border-t border-ms-border px-6 py-4 text-center text-xs text-ms-text-secondary">
        Architecture Review Board &middot; Powered by Microsoft Azure
      </footer>
    </div>
  );
}
