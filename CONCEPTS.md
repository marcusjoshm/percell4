# Concepts

Shared domain vocabulary for this project — entities, named processes, and status concepts with project-specific meaning. Seeded with core domain vocabulary, then accretes as ce-compound and ce-compound-refresh process learnings; direct edits are fine. Glossary only, not a spec or catch-all.

Seeded from the imaging-layer domain — the artifacts a dataset holds and the viewer displays. Other areas of the project are not yet covered; further refresh runs will add them.

## Relationships

A Dataset owns Channels, Label Sets, and Masks, each in its own namespace and each addressed by name. Names are unique *within* a namespace but not across them, and that is the source of most confusion here: the same name can denote a Channel, a Label Set, and a Mask simultaneously, and each collision has different consequences. A Segmentation is not a fourth kind of artifact — it is a Label Set that no Mask has claimed.

## Imaging artifacts

### Dataset
One imaging acquisition and everything derived from it, held in a single file. A Dataset is the unit a researcher opens, and every other concept here is addressed relative to one.

### Channel
A named intensity image within a Dataset — one detector, stain, or acquisition band. A Channel may carry FLIM siblings derived from the same acquisition: a decay record and a phasor record, both addressed by the Channel's own name. Deleting a Channel is permanent and takes those siblings with it.

### Label Set
An integer-labelled image where each distinct non-zero value identifies one detected object. Label Sets are the storage form for anything object-labelled; whether a given Label Set is treated as a Segmentation depends on whether a Mask has claimed its name.

### Segmentation
A Label Set whose name is *not* also a Mask name — the per-object labelling a researcher chose as the active object set for a Dataset.

Segmentation is a derived category, not a separate artifact: every Segmentation is a Label Set, but a Label Set shadowed by a same-named Mask is excluded. Any code that offers the user a list of Segmentations must apply that exclusion; a list built from Label Sets alone will offer Masks as if they were object labellings, which is the defect this distinction exists to prevent.

### Mask
A per-pixel selection over a Dataset — which pixels are in play, as opposed to which object each pixel belongs to. A Mask answers "where", a Segmentation answers "which one".

Masks and Segmentations occupy separate namespaces but share a display form, so the viewer records which of the two a given layer represents rather than inferring it. Inference by name or by array shape is unreliable and has produced real crashes.

### Phasor ROI
A region a researcher draws in phasor space to select pixels by lifetime rather than by position. Applying one produces a Mask, which is where a Phasor ROI's name can collide with an existing Channel or Label Set name.

## Viewer layers

### Layer
One displayed item in the viewer — a Channel, a Segmentation, a Mask, or a transient overlay. The viewer's layer names form a single flat namespace shared by every layer type, so a name collision between two *different* kinds of artifact is possible and must be refused rather than resolved silently.

### Layer type tag
The record the viewer keeps of which domain artifact a Layer represents, written when the Layer is created. It is the authoritative answer to "what is this layer" — more reliable than the layer's name, which a user can change, and than its data shape, which Masks and Segmentations share.

### Preview layer
A transient Layer the viewer displays but the Dataset never stores — a live preview while a researcher tunes a threshold or draws an ROI. Preview layers are marked by a naming convention that excludes them from every list of real artifacts, so a preview can never be mistaken for a saved Segmentation or Mask, and they are discarded rather than persisted when the tool that owns them closes.

## Flagged ambiguities

- **"Segmentation" and "label set" are not interchangeable.** Every Segmentation is a Label Set; a Label Set whose name is also a Mask name is not a Segmentation. Storage-level code tends to say *labels*, while session- and user-facing code says *segmentation* — when the two appear to disagree, the exclusion rule is the reason.
- **"Mask" and "segmentation" answer different questions.** Which pixels (Mask) versus which object each pixel belongs to (Segmentation). They are stored separately and displayed identically, which is why the viewer tags layers by type instead of guessing.
