import { useEffect, useState } from "react";
import { ValidationEntry } from "./data/types.ts";
import logo from "./assets/logo.png";
import "./index.css";
import FileUpload from "./components/FileUpload";
import ValidationTable from "./components/ValidationTable";
import IaCResults from "./components/IaCResults.tsx";

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
    <div className="flex flex-col bg-marigold items-center min-h-screen gap-y-5 w-screen overflow-x-clip">
      <img src={logo} className="absolute top-2 left-2 w-40"></img>
      <div className="text-blue text-4xl pt-16 font-bold font-main">
        Sun Life ASD Validator
      </div>
      <div className="flex flex-col gap-y-8 items-center">
        <FileUpload setValidateResult={setValidateResult} setIaCResult={setIaCResult} setNewestResult={setNewestResult}/>
        <div id="ValidationTable">
          {validateResult && (
            <ValidationTable validateResult={validateResult} />
          )}
        </div>

        <div id="IaCResults">
          {IaCResult && (
            <IaCResults results={IaCResult} />
          )}
        </div>
      </div>
    </div>
  );
}
