// Reads "pattern<TAB>text" lines (each base64 of UTF-8) and prints, per line, whether the text matches
// the pattern as Maestro reads what the maestro driver sends: "(?-ims)" + pattern, compiled with Maestro's
// Kotlin Regex options (IGNORE_CASE, DOT_MATCHES_ALL, MULTILINE; Kotlin adds UNICODE_CASE), whole text
// (Matcher.matches()), or ERROR when java.util.regex refuses it. Run as: java Match.java < cases
// With the argument raw, the pattern is compiled as given (no "(?-ims)" added): the exact text a flow holds.
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.Base64;
import java.util.regex.Pattern;

public class Match {
    static final int MAESTRO = Pattern.CASE_INSENSITIVE | Pattern.UNICODE_CASE | Pattern.DOTALL | Pattern.MULTILINE;

    public static void main(String[] args) throws IOException {
        BufferedReader in = new BufferedReader(new InputStreamReader(System.in, StandardCharsets.UTF_8));
        Base64.Decoder b64 = Base64.getDecoder();
        String same = args.length > 0 && args[0].equals("raw") ? "" : "(?-ims)";
        String line;
        while ((line = in.readLine()) != null) {
            String[] f = line.split("\t", -1);
            String p = new String(b64.decode(f[0]), StandardCharsets.UTF_8);
            String s = new String(b64.decode(f[1]), StandardCharsets.UTF_8);
            String r;
            try {
                r = String.valueOf(Pattern.compile(same + p, MAESTRO).matcher(s).matches());
            } catch (RuntimeException e) {
                r = "ERROR";
            }
            System.out.println(r);
        }
    }
}
