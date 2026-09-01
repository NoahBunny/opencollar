package com.bunnytasker;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Random;
import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

/**
 * The 144 preloaded veneration tasks, drawn at random within a category.
 *
 * <p>A byte-identical copy of Lion's Share's picker, on the other side of the
 * table. There it draws a task for the Lion to IMPOSE; here it draws one the
 * bunny may OFFER — the same 144 lines either way, which is the point. If the
 * two catalogues could drift, a line offered from one and enforced from the
 * other would differ by a capital and be unsatisfiable, so both apps read the
 * same derived resource and a test asserts the two files are byte-identical.
 *
 * <p>Fed by `res/raw/veneration_tasks.json`, which is DERIVED from
 * `veneration-tasks.json` by `scripts/make-veneration-md.py` — including the
 * category display titles, so the picker cannot disagree with the document.
 * Never hand-edit either copy.
 */
final class VenerationTasks {

    /** Pseudo-category meaning "draw from everything". */
    static final String ANY = "*";

    static final class Task {
        final String id;
        final String category;
        final int reps;
        final String text;

        Task(String id, String category, int reps, String text) {
            this.id = id;
            this.category = category;
            this.reps = reps;
            this.text = text;
        }
    }

    static final class Category {
        final String key;
        final String title;
        final int count;

        Category(String key, String title, int count) {
            this.key = key;
            this.title = title;
            this.count = count;
        }
    }

    private final List<Category> categories;
    private final List<Task> tasks;
    private String lastDrawnId = "";

    private VenerationTasks(List<Category> categories, List<Task> tasks) {
        this.categories = categories;
        this.tasks = tasks;
    }

    /**
     * Parse the shipped catalogue.
     *
     * @throws JSONException if the resource is malformed — deliberately not
     *     swallowed. An empty picker offering the Lion nothing, with no
     *     explanation, is worse than a caller that can say why.
     */
    static VenerationTasks parse(String json) throws JSONException {
        JSONObject root = new JSONObject(json);
        List<Category> cats = new ArrayList<>();
        JSONArray ja = root.optJSONArray("categories");
        for (int i = 0; ja != null && i < ja.length(); i++) {
            JSONObject c = ja.getJSONObject(i);
            cats.add(new Category(c.getString("key"), c.getString("title"), c.optInt("count")));
        }
        List<Task> ts = new ArrayList<>();
        JSONArray jt = root.optJSONArray("tasks");
        for (int i = 0; jt != null && i < jt.length(); i++) {
            JSONObject t = jt.getJSONObject(i);
            ts.add(new Task(t.getString("id"), t.getString("category"), t.optInt("reps", 1), t.getString("text")));
        }
        return new VenerationTasks(Collections.unmodifiableList(cats), Collections.unmodifiableList(ts));
    }

    List<Category> categories() {
        return categories;
    }

    int size() {
        return tasks.size();
    }

    /** Every task in a category, or all of them for {@link #ANY}. */
    List<Task> inCategory(String key) {
        if (ANY.equals(key)) return tasks;
        List<Task> out = new ArrayList<>();
        for (Task t : tasks) {
            if (t.category.equals(key)) out.add(t);
        }
        return out;
    }

    /**
     * Draw one task at random from a category.
     *
     * <p>Avoids repeating the immediately previous draw, so tapping "another"
     * always visibly changes something — a picker that can hand back the same
     * line twice reads as broken rather than random. With one task in the
     * category there is nothing else to give, and it returns that.
     *
     * @return null when the category is empty (never a placeholder Task — a
     *     silently-substituted task is one the Lion did not choose)
     */
    Task draw(String key, Random rng) {
        List<Task> pool = inCategory(key);
        if (pool.isEmpty()) return null;
        Task pick = pool.get(rng.nextInt(pool.size()));
        if (pool.size() > 1 && pick.id.equals(lastDrawnId)) {
            // One re-roll is enough: it cannot land on the same id twice in a
            // row and cannot loop, which a while-loop over a stubborn rng could.
            Task second = pool.get(rng.nextInt(pool.size()));
            if (!second.id.equals(lastDrawnId)) pick = second;
            else {
                int idx = pool.indexOf(pick);
                pick = pool.get((idx + 1) % pool.size());
            }
        }
        lastDrawnId = pick.id;
        return pick;
    }
}
