import { Bath, BedDouble, Image as ImageIcon, Plus, Search, Trash2 } from "lucide-react";
import type { PlaybookBlock, PropertyPlaybook, PropertyRecord, RuntimeConfigValues } from "../api";
import { effectiveAutoReplyInput } from "../playbookState";
import {
  classNames,
  EmptyState,
  formatMoney,
  propertyAvailabilityLabel,
  propertyAvailabilityTone,
  replacePlaceholders,
} from "../viewHelpers";
import "./properties.css";

export function PropertiesView({
  properties,
  playbooks,
  config,
  selectedPropertyIds,
  setSelectedPropertyIds,
  search,
  setSearch,
  onNewProperty,
  onManage,
  onDeleteSelected,
}: {
  properties: PropertyRecord[];
  playbooks: PropertyPlaybook[];
  config: RuntimeConfigValues;
  selectedPropertyIds: string[];
  setSelectedPropertyIds: React.Dispatch<React.SetStateAction<string[]>>;
  search: string;
  setSearch: (value: string) => void;
  onNewProperty: () => void;
  onManage: (property: PropertyRecord) => void;
  onDeleteSelected: (propertyIds: string[]) => void;
}) {
  const visiblePropertyIds = properties.map((property) => property.property_id);
  const selectedVisibleIds = visiblePropertyIds.filter((propertyId) => selectedPropertyIds.includes(propertyId));
  const allVisibleSelected = visiblePropertyIds.length > 0 && selectedVisibleIds.length === visiblePropertyIds.length;

  function togglePropertySelection(propertyId: string) {
    setSelectedPropertyIds((current) =>
      current.includes(propertyId) ? current.filter((item) => item !== propertyId) : [...current, propertyId],
    );
  }

  function toggleSelectAllVisible() {
    setSelectedPropertyIds((current) => {
      const visible = new Set(visiblePropertyIds);
      if (allVisibleSelected) return current.filter((propertyId) => !visible.has(propertyId));
      return [...current, ...visiblePropertyIds.filter((propertyId) => !current.includes(propertyId))];
    });
  }

  return (
    <section className="propertiesPage">
      <div className="pageToolbar">
        <div className="toolbarLeft">
          <div className="searchBox">
            <Search size={18} />
            <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search listings" />
          </div>
          <span>{properties.length} listings managed</span>
          <label className="selectAllControl">
            <input type="checkbox" checked={allVisibleSelected} disabled={visiblePropertyIds.length === 0} onChange={toggleSelectAllVisible} />
            Select all visible
          </label>
        </div>
        <div className="toolbarActions">
          {selectedPropertyIds.length > 0 && (
            <div className="bulkActionBar">
              <strong>{selectedPropertyIds.length} selected</strong>
              <button className="dangerButton" onClick={() => onDeleteSelected(selectedPropertyIds)}>
                <Trash2 size={15} /> Delete selected
              </button>
            </div>
          )}
          <button className="primaryButton" onClick={onNewProperty}><Plus size={17} /> New Listing</button>
        </div>
      </div>
      <div className="listingStack">
        {properties.length === 0 && <EmptyState title="No properties yet" body="Create or seed properties to manage listing workflows." />}
        {properties.map((property) => {
          const enabledMedia = property.media.filter((media) => media.enabled);
          const playbook = playbooks.find((item) => item.property_id === property.property_id);
          const autoReplyInput = effectiveAutoReplyInput(playbook);
          return (
            <article key={property.property_id} className="listingCard">
              <ListingAutoReplyPreview
                property={property}
                playbook={playbook}
                blocks={autoReplyInput.initial_reply_blocks}
                config={config}
              />
              <div className="listingBody">
                <div className="listingTop">
                  <div>
                    <div className="badgeRow">
                      <span className={classNames("badge", propertyAvailabilityTone(property.status))}>{propertyAvailabilityLabel(property.status)}</span>
                    </div>
                    <h2>{property.property_name}</h2>
                    <p>
                      <span>Rental</span>
                      <span><BedDouble size={15} /> {property.bedrooms ?? "-"} BR</span>
                      <span><Bath size={15} /> {property.bathrooms ?? "-"} Bath</span>
                    </p>
                  </div>
                  <div className="listingPrice">
                    <strong>{formatMoney(property.asking_rent)}</strong>
                    <span>{property.available_from || "Availability not set"}</span>
                  </div>
                </div>
                <div className="listingMeta">
                  <div><span>Gallery</span><strong>{enabledMedia.length} enabled</strong></div>
                </div>
                <div className="listingActions">
                  <label className="listingSelect">
                    <input
                      type="checkbox"
                      checked={selectedPropertyIds.includes(property.property_id)}
                      onChange={() => togglePropertySelection(property.property_id)}
                    />
                    Select
                  </label>
                  <button className="dangerButton" onClick={() => onDeleteSelected([property.property_id])}>
                    <Trash2 size={15} /> Delete
                  </button>
                  <button className="primaryButton" onClick={() => onManage(property)}>Edit</button>
                </div>
              </div>
            </article>
          );
        })}
      </div>
    </section>
  );
}

function ListingAutoReplyPreview({
  property,
  playbook,
  blocks,
  config,
}: {
  property: PropertyRecord;
  playbook?: PropertyPlaybook | null;
  blocks: PlaybookBlock[];
  config: RuntimeConfigValues;
}) {
  const enabled = playbook?.enabled ?? false;
  const messageBlocks = blocks.filter((block) => block.type === "message").slice(0, 2);
  const hasGallery = blocks.some((block) => block.type === "gallery");
  const enabledMediaCount = property.media.filter((item) => item.enabled).length;

  return (
    <div className={classNames("listingReplyPreview", !enabled && "disabled")}>
      <div className="replyPreviewTop">
        <span>{enabled ? "Available reply" : "Auto reply off"}</span>
      </div>
      <div className="replyPreviewChat">
        {enabled && messageBlocks.length > 0 ? (
          messageBlocks.map((block, index) => (
            <div key={`${property.property_id}-${index}`} className="replyPreviewBubble">
              {replacePlaceholders(block.text || "", property, config)}
            </div>
          ))
        ) : (
          <div className="replyPreviewEmpty">{enabled ? "No message configured" : "Enable Auto Replies to send messages"}</div>
        )}
        {enabled && hasGallery && (
          <div className="replyPreviewMedia">
            <ImageIcon size={14} />
            {enabledMediaCount} media
          </div>
        )}
      </div>
    </div>
  );
}
