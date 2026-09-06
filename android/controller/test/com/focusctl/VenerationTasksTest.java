package com.focusctl;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.HashSet;
import java.util.List;
import java.util.Random;
import java.util.Set;
import org.json.JSONException;
import org.junit.jupiter.api.Test;

/**
 * Spec for the veneration picker, and a guard on the catalogue it ships.
 *
 * <p>Why the data itself is asserted here: every one of these tasks capitalises
 * the Lion's pronouns mid-sentence, and the picker turns `task_randcaps` on, so
 * the string IS the enforced form. A task that loses a capital between the
 * source document and the shipped resource is one the bunny is required to type
 * and cannot get right — and the failure surfaces on a lockscreen, at the worst
 * possible moment, with no way to argue.
 */
public class VenerationTasksTest {

    private static final String SAMPLE =
        "{\"categories\":["
            + "{\"key\":\"ownership\",\"title\":\"Ownership & belonging\",\"count\":2},"
            + "{\"key\":\"long\",\"title\":\"Long-form (single rep)\",\"count\":1}],"
            + "\"tasks\":["
            + "{\"id\":\"ven-001\",\"category\":\"ownership\",\"reps\":5,\"text\":\"I belong to the Lion.\"},"
            + "{\"id\":\"ven-002\",\"category\":\"ownership\",\"reps\":5,\"text\":\"I am Theirs.\"},"
            + "{\"id\":\"ven-140\",\"category\":\"long\",\"reps\":1,\"text\":\"A longer one.\"}]}";

    /** The shipped resource, found by walking up to the repo root. */
    private static File shipped() {
        File dir = new File(".").getAbsoluteFile();
        for (int i = 0; i < 6 && dir != null; i++, dir = dir.getParentFile()) {
            File f = new File(dir, "android/controller/res/raw/veneration_tasks.json");
            if (f.isFile()) return f;
            f = new File(dir, "controller/res/raw/veneration_tasks.json");
            if (f.isFile()) return f;
        }
        return null;
    }

    private static VenerationTasks shippedCatalogue() throws Exception {
        File f = shipped();
        assertNotNull(f, "could not locate res/raw/veneration_tasks.json from " + new File(".").getAbsolutePath());
        return VenerationTasks.parse(new String(Files.readAllBytes(f.toPath()), StandardCharsets.UTF_8));
    }

    // ── parsing ───────────────────────────────────────────────────────

    @Test
    void parsesCategoriesInDocumentOrder() throws Exception {
        VenerationTasks v = VenerationTasks.parse(SAMPLE);
        assertEquals(2, v.categories().size());
        assertEquals("ownership", v.categories().get(0).key);
        assertEquals("Ownership & belonging", v.categories().get(0).title);
        assertEquals(2, v.categories().get(0).count);
        assertEquals(3, v.size());
    }

    @Test
    void malformedJsonThrowsRatherThanYieldingAnEmptyPicker() {
        // A picker that silently offers nothing gives the Lion no way to tell
        // "no tasks" from "the file is broken".
        assertThrows(JSONException.class, () -> VenerationTasks.parse("{not json"));
    }

    // ── selecting ─────────────────────────────────────────────────────

    @Test
    void inCategoryReturnsOnlyThatCategory() throws Exception {
        VenerationTasks v = VenerationTasks.parse(SAMPLE);
        List<VenerationTasks.Task> own = v.inCategory("ownership");
        assertEquals(2, own.size());
        for (VenerationTasks.Task t : own) assertEquals("ownership", t.category);
    }

    @Test
    void anyDrawsFromEverything() throws Exception {
        VenerationTasks v = VenerationTasks.parse(SAMPLE);
        assertEquals(3, v.inCategory(VenerationTasks.ANY).size());
    }

    @Test
    void drawStaysInsideTheChosenCategory() throws Exception {
        VenerationTasks v = VenerationTasks.parse(SAMPLE);
        Random rng = new Random(1234);
        for (int i = 0; i < 40; i++) {
            assertEquals("ownership", v.draw("ownership", rng).category);
        }
    }

    @Test
    void drawNeverRepeatsTheLineItJustGave() throws Exception {
        // "Draw another" that hands back the same task reads as broken.
        VenerationTasks v = shippedCatalogue();
        Random rng = new Random(7);
        String prev = "";
        for (int i = 0; i < 300; i++) {
            VenerationTasks.Task t = v.draw("patience", rng);  // smallest real pool (10)
            assertNotNull(t);
            assertFalse(t.id.equals(prev), "drew " + t.id + " twice running");
            prev = t.id;
        }
    }

    @Test
    void aSingleTaskCategoryKeepsReturningItsOneTask() throws Exception {
        String one = "{\"categories\":[{\"key\":\"solo\",\"title\":\"Solo\",\"count\":1}],"
            + "\"tasks\":[{\"id\":\"x\",\"category\":\"solo\",\"reps\":1,\"text\":\"only\"}]}";
        VenerationTasks v = VenerationTasks.parse(one);
        Random rng = new Random(3);
        assertEquals("x", v.draw("solo", rng).id);
        assertEquals("x", v.draw("solo", rng).id, "with nothing else to give it must still give this");
    }

    @Test
    void anEmptyCategoryYieldsNullRatherThanSomethingUnchosen() throws Exception {
        VenerationTasks v = VenerationTasks.parse(SAMPLE);
        assertNull(v.draw("no-such-category", new Random(1)));
    }

    // ── the shipped catalogue ─────────────────────────────────────────

    @Test
    void theShippedCatalogueParsesAndIsComplete() throws Exception {
        VenerationTasks v = shippedCatalogue();
        assertEquals(144, v.size(), "144 tasks are documented; the resource must carry all of them");
        int summed = 0;
        for (VenerationTasks.Category c : v.categories()) {
            assertEquals(c.count, v.inCategory(c.key).size(), c.key + " count disagrees with its tasks");
            summed += c.count;
        }
        assertEquals(v.size(), summed, "every task must belong to a listed category");
    }

    @Test
    void everyTaskIsUsableAsSetOut() throws Exception {
        VenerationTasks v = shippedCatalogue();
        Set<String> ids = new HashSet<>();
        for (VenerationTasks.Task t : v.inCategory(VenerationTasks.ANY)) {
            assertTrue(ids.add(t.id), "duplicate id " + t.id);
            assertFalse(t.text.trim().isEmpty(), t.id + " has no text");
            assertTrue(t.reps >= 1, t.id + " has reps " + t.reps + "; a task worth 0 reps is not a task");
            assertFalse(t.text.contains("\n"), t.id + " spans lines — the task field is single-value");
        }
    }

    @Test
    void theShippedTextIsCharacterIdenticalToTheSourceDocument() throws Exception {
        // The invariant that actually matters. res/raw is DERIVED from
        // veneration-tasks.json, and the derived copy is what the bunny is made
        // to type: with randcaps on, one lost capital is a task they cannot
        // satisfy and cannot argue with. So compare the strings, exactly.
        //
        // An earlier version of this test tried to police the capitalisation
        // directly — flag any lowercase "them"/"their". It fired on ven-011,
        // "I built them and handed over the keys", where *them* is the locks.
        // A regex cannot tell the Lion from a courier, which is the same reason
        // collar-pronoun-check.sh is a review queue rather than a judge — and
        // task text is excluded from that check outright, since the Lion set
        // these strings and Bunny is only reproducing Them. Comparing against
        // the source needs no judgement and catches case drift outright.
        File src = null;
        File dir = new File(".").getAbsoluteFile();
        for (int i = 0; i < 6 && dir != null; i++, dir = dir.getParentFile()) {
            File f = new File(dir, "veneration-tasks.json");
            if (f.isFile()) {
                src = f;
                break;
            }
        }
        assertNotNull(src, "could not locate the source veneration-tasks.json");

        org.json.JSONArray source =
            new org.json.JSONObject(new String(Files.readAllBytes(src.toPath()), StandardCharsets.UTF_8))
                .getJSONArray("tasks");
        java.util.Map<String, String> want = new java.util.HashMap<>();
        for (int i = 0; i < source.length(); i++) {
            org.json.JSONObject t = source.getJSONObject(i);
            want.put(t.getString("id"), t.getString("task_text"));
        }

        VenerationTasks v = shippedCatalogue();
        assertEquals(want.size(), v.size(), "the shipped catalogue drops tasks the source defines");
        for (VenerationTasks.Task t : v.inCategory(VenerationTasks.ANY)) {
            assertEquals(want.get(t.id), t.text, t.id + " differs from the source document");
        }
    }
}
