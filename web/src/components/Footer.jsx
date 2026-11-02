export default function Footer({ notice }) {
  return (
    <footer className="footer">
      {notice && <p>{notice}</p>}
      <p>Search over public FDA drug-label text. Not medical advice.</p>
    </footer>
  )
}
