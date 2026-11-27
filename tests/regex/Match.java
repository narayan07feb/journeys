// Reads "pattern<TAB>text" lines (each base64 of UTF-8) and prints, per line, whether the text matches
// the pattern as the maestro driver sends it: "(?-ims)" + pattern, whole text (Matcher.matches()),
// or ERROR when java.util.regex refuses it. Run as a single-file program: java Match.java < cases
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.Base64;
import java.util.regex.Pattern;

public class Match {
    public static void main(String[] args) throws IOException {
        BufferedReader in = new BufferedReader(new InputStreamReader(System.in, StandardCharsets.UTF_8));
        Base64.Decoder b64 = Base64.getDecoder();
        String line;
        while ((line = in.readLine()) != null) {
            String[] f = line.split("\t", -1);
            String p = new String(b64.decode(f[0]), StandardCharsets.UTF_8);
            String s = new String(b64.decode(f[1]), StandardCharsets.UTF_8);
            String r;
            try {
                r = String.valueOf(Pattern.compile("(?-ims)" + p).matcher(s).matches());
            } catch (RuntimeException e) {
                r = "ERROR";
            }
            System.out.println(r);
        }
    }
}
