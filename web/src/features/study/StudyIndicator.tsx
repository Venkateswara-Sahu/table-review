import { Link } from "react-router-dom";
import { FlaskConical } from "lucide-react";
import { useStudy } from "./StudyRecorder";

export function StudyIndicator() {
  const study = useStudy();
  if (!study) return null;
  return (
    <Link to="/study" className="study-badge" title="Open the study trial">
      <FlaskConical size={13} aria-hidden="true" />
      <span>Study</span>
      <span className="study-badge-condition">{study.config.condition}</span>
    </Link>
  );
}
