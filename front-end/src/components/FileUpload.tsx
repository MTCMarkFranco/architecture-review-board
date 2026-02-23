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

    const apiUrl = "http://127.0.0.1:5000/validateasd";

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
      <label className="flex flex-row z-10 bg-clip-padding gap-y-4 px-2 py-2 items-center justify-center border-blue border-2 w-36 h-full cursor-pointer rounded-lg bg-blue text-white hover:scale-105 transition ease-in-out">
        <FontAwesomeIcon icon={faArrowUpFromBracket} className="mr-2" />
        Upload ASD
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
          <div>
            <FontAwesomeIcon
              icon={faCircleCheck}
              className="text-emerald-600 mr-1"
            />
            Upload success!
          </div>

          <div>
            Uploaded: <strong>{selectedFile.name}</strong>
          </div>

          <div className="flex flex-row gap-x-2">
            <button
              className="bg-blue border-black text-white p-2 w-36 py-2.5 rounded-lg hover:scale-105 transition ease-in-out"
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
                  Validate ASD
                </div>
              )}
            </button>

            <button
              className="bg-blue border-black text-white p-2 w-36 py-2.5 rounded-lg hover:scale-105 transition ease-in-out"
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
