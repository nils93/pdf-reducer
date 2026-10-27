# PDF Reducer

Lokale Browser-App zum Komprimieren von PDFs. PDFs lassen sich über den Dateidialog auswählen oder per Drag-and-drop hinzufügen. Ein × neben jeder Datei entfernt sie aus der Warteschlange. Der Fortschrittsbalken zeigt den Stand der einzelnen PDFs. Bei genau einer Datei lädt **Speichern unter** die komprimierte PDF mit ihrem ursprünglichen Namen herunter. Bei mehreren Dateien startet **Alle PDFs als ZIP herunterladen** den Download `komprimierte-pdfs.zip`; die ursprünglichen PDF-Namen bleiben im ZIP erhalten. Für einen Speicherortdialog in Firefox muss in den Download-Einstellungen die Nachfrage nach dem Speicherort aktiviert sein.

## Start

Voraussetzung: Python 3, Ghostscript und MuPDF. Beide Werkzeuge sind auf diesem Rechner vorhanden. Unter Ubuntu/Debian:

```bash
sudo apt install ghostscript mupdf-tools
python3 pdf_reducer.py
```

Die Anwendung öffnet sich unter `http://127.0.0.1:8766/`. Mit `Ctrl+C` im Terminal wird sie beendet. Falls der Port belegt ist:

```bash
python3 pdf_reducer.py --port 8767
```

Für exportierte iPhone-Fotos ist **Fotos / Scans – gut lesbar** voreingestellt. Jede sichtbare Seite wird auf höchstens 1600 Pixel an der längsten Kante gerendert, mit JPEG-Qualität 65 komprimiert und wieder in eine PDF eingebettet. Die Begrenzung hängt von Pixeln statt von der oft ungewöhnlichen Druckgröße solcher PDFs ab. Seitengrößen und Ausrichtung bleiben erhalten. **Stärker verkleinern** verwendet 1200 Pixel und JPEG-Qualität 55. Der Fortschritt wird pro Seite angezeigt; **Abbrechen** beendet auch den laufenden PDF-Prozess.

Die Foto-Modi erstellen Bild-PDFs: Eine vorhandene Suchtextebene, Links, Formularfunktionen und andere interaktive Inhalte werden dabei nicht übernommen. Fotos und Scans können Details verlieren; die Lesbarkeit sollte vor dem Versand geprüft werden. Für PDFs mit auswählbarem Text gibt es **Dokumente – Text und Vektoren erhalten**; hier wird nur eingebettetes Bildmaterial heruntergerechnet.

Originaldateien bleiben unverändert; die App verarbeitet alles lokal. Sie bietet nur Ergebnisse an, die tatsächlich kleiner sind. Gleiche Dateinamen müssen vor dem ZIP-Download aus der Liste entfernt werden, damit die Einträge eindeutig bleiben. Nach einem Update den Python-Server vollständig mit `Ctrl+C` beenden, erneut starten und die Seite neu laden; die neue Oberfläche zeigt **Version 3**.

## Grenzen

- Maximal 500 MB je PDF; der gesamte ZIP-Upload an den lokalen Server darf höchstens 1 GB umfassen.
- Jeder Verarbeitungsschritt hat ein Zeitlimit von drei Minuten.
- Es gibt keine feste Zielgröße. Bereits stark komprimierte PDFs werden eventuell kaum kleiner.

## Verifikation

`python3 -m unittest discover -s tests -v` prüft mit erzeugten Foto-PDFs die tatsächliche Größenreduktion, maximale Bildgröße, Seitengrößen, Ablehnung größerer Ergebnisse, Abbruch, verständliche Fehlermeldungen und ZIP-Dateinamen. Es werden keine persönlichen PDFs für die Tests verwendet.
