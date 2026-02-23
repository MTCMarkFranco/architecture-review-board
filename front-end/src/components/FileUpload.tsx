import { useState } from "react";
import { ValidationEntry } from "../data/types.ts";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import {
  faArrowUpFromBracket,
  faCircleCheck,
  faThumbsUp,
  faCircleNotch,
  faGear,
} from "@fortawesome/free-solid-svg-icons";

import axios from "axios";

export interface FileUploadProps {
  setValidateResult: (response: ValidationEntry[]) => void;
  setIaCResult: (response: string[]) => void;
  setNewestResult: (response: string) => void;
}

export default function FileUpload({ setValidateResult, setIaCResult, setNewestResult }: FileUploadProps) {
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [isValidating, setIsValidating] = useState(false);
  const [isGenerating, setIsGenerating] = useState(false);

  const handleFileValidate = async () => {
    if (!selectedFile) {
      alert("No file uploaded");
      return;
    }

    setIsValidating(true);

    const formData = new FormData();
    formData.append("file", selectedFile, selectedFile.name);

    const apiUrl = "http://127.0.0.1:5000/validatearb";

    await axios
      .post(apiUrl, formData, {
        headers: {
          "Content-Type": "multipart/form-data",
        },
      })
      .then((response) => {
        console.log("File validated successfully", response.data);
        setValidateResult(response.data);
        setNewestResult('ValidationTable');
      })
      .catch((error) => {
        console.error("Error uploading file", error);
      })
      .finally(() => {
        setIsValidating(false);
      });
  };

  const handleIACGeneration = async () => {
    if(!selectedFile) {
      alert("No file uploaded");
      return;
    }

    setIsGenerating(true);

    const formData = new FormData();
    formData.append("file", selectedFile, selectedFile.name);

    const apiUrl = "http://127.0.0.1:5000/geniac";

    await axios
      .post(apiUrl, formData, {
        headers: {
          "Content-Type": "multipart/form-data",
        },
      })
      .then((response) => {
        console.log("IaC generated successfully", response.data);
        setIaCResult(response.data);
        setNewestResult('IaCResults')
      })
      .catch((error) => {
        console.error("Error uploading file", error);
      })
      .finally(() => {
        setIsGenerating(false);
      });
  };

  return (
    <div className="relative flex w-full h-full flex-col gap-y-3 items-center font-main">
      <label className="flex z-10 bg-clip-padding px-6 py-3 items-center justify-center gap-2 border border-ms-blue w-52 cursor-pointer rounded-md bg-ms-blue text-white text-center hover:bg-ms-accent transition-colors duration-150 shadow-sm">
        <FontAwesomeIcon icon={faArrowUpFromBracket} className="flex-shrink-0" />
        <span>Upload &amp; Validate</span>
        <input
          name="document"
          type="file"
          hidden
          accept=".pdf"
          onChange={(e) => {
            if (e.target.files) {
              setSelectedFile(e.target.files![0]);
            }
          }}
        />
      </label>

      {selectedFile && (
        <div className="flex flex-col gap-y-3 items-center w-full">
          <div className="text-ms-success">
            <FontAwesomeIcon
              icon={faCircleCheck}
              className="mr-1"
            />
            Upload success!
          </div>

          <div className="text-ms-text">
            Uploaded: <strong>{selectedFile.name}</strong>
          </div>

          <div className="flex flex-row gap-x-3">
            <button
              className="bg-ms-blue text-white px-4 py-2.5 rounded-md hover:bg-ms-accent transition-colors duration-150 shadow-sm disabled:opacity-50 disabled:cursor-not-allowed"
              onClick={handleFileValidate}
              disabled={isValidating}
            >
              {isValidating ? (
                <div className="animate-spin w-auto h-[1.4rem]">
                  <FontAwesomeIcon icon={faCircleNotch} />
                </div>
              ) : (
                <div>
                  <FontAwesomeIcon icon={faThumbsUp} className="mr-2" />
                  Upload & Validate
                </div>
              )}
            </button>

            <button
              className="bg-ms-purple text-white px-4 py-2.5 rounded-md hover:opacity-90 transition-colors duration-150 shadow-sm disabled:opacity-50 disabled:cursor-not-allowed"
              onClick={handleIACGeneration}
              disabled={isGenerating}
            >
              {isGenerating ? (
                <div className="animate-spin w-auto h-[1.4rem]">
                  <FontAwesomeIcon icon={faCircleNotch} />
                </div>
              ) : (
                <div>
                  <FontAwesomeIcon icon={faGear} className="mr-2" />
                  Generate IaC
                </div>
              )}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
