export function LegalNotice() {
  return (
    <div className="space-y-2 text-sm">
      <p className="font-medium">Before you send anything</p>
      <ul className="text-muted-foreground list-disc space-y-1.5 pl-5">
        <li>
          <strong className="text-foreground">US (CAN-SPAM):</strong> use your real name, a truthful
          subject, and honour opt-outs promptly. Reachout adds an opt-out line to every email and
          suppresses anyone who asks to stop.
        </li>
        <li>
          <strong className="text-foreground">EU and UK (GDPR, PECR):</strong> cold emailing
          individuals is heavily restricted. Avoid emailing individuals in the EU or UK, especially
          in service mode.
        </li>
        <li>
          <strong className="text-foreground">India (DPDP Act):</strong> collect only publicly
          published business contact details. Reachout stores the source URL of every lead so you
          can say why you hold an address.
        </li>
        <li>Use a separate Gmail account for cold outreach.</li>
      </ul>
    </div>
  )
}
