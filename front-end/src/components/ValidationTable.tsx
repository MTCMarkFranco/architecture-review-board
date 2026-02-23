import { ValidationEntry } from "../data/types.ts";

export interface ValidationTableProps {
  validateResult: ValidationEntry[] | null;
}

export default function ValidationTable({
  validateResult,
}: ValidationTableProps) {
  return (
    <div className="px-14 overflow-x-auto font-main pb-4">
      <div className="text-2xl font-bold pb-2 text-blue">
        Validation Results
      </div>
      {validateResult ? (
        <table className="border-collapse text-center min-w-full">
          <thead>
            <tr className="text-black">
              <th className="border-2 border-blue p-2 bg-warmgrey">Type</th>
              <th className="border-2 border-blue p-2 bg-warmgrey">Issue</th>
              <th className="border-2 border-blue p-2 bg-warmgrey">
                Description
              </th>
              <th className="border-2 border-blue p-2 bg-warmgrey">
                Principles
              </th>
            </tr>
          </thead>
          <tbody>
            {validateResult.map((entry, index) => (
              <tr key={index}>
                <td className="border-2 border-blue p-2 bg-warmyellow">
                  {entry.Type}{" "}
                  {entry.Mandatory && (
                    <span className="text-red-600">(Mandatory)</span>
                  )}
                </td>
                <td className="border-2 border-blue p-2 bg-warmyellow">
                  {entry.Issue}
                </td>
                <td className="border-2 border-blue p-2 bg-warmyellow">
                  {entry.Description}
                </td>
                <td className="border-2 border-blue p-2 bg-warmyellow">
                  {entry.Principles}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p>No validation results to display.</p>
      )}
    </div>
  );
}
