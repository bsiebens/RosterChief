/**
 * Sample content for the parts of the member app that are designed but not connected to the backend yet. Every use sits next to a
 * `<SampleBadge what="...">`; docs/redesign-backend-prompt.md lists what each one needs. Delete an export when its block goes live.
 */

/** event-lineup: the published line-up of a game and the attendance counts (needs a portal endpoint over events.EventLineup). */
export const sampleLineup = {
	players: [
		{ number: 4, name: 'Janssens' },
		{ number: 7, name: 'De Smet' },
		{ number: 12, name: 'Vermeulen', mine: true },
		{ number: 14, name: 'Peeters' },
		{ number: 17, name: 'Claes' }
	],
	more: 9,
	going: 14,
	out: 2,
	waiting: 1
};

/** event-duties: parent duties (timekeeper, canteen ...) a member can volunteer for (needs event tasks in the portal API). */
export const sampleDuties = [{ id: 'timekeeper', title: 'Timekeeper', note: 'Still open · reply by Wednesday' }];

/** event-attachments: files the club attached to an event (needs a member download route for events.EventAttachment). */
export const sampleAttachments = [{ id: 'travel', name: 'Travel and parking info.pdf', size: '182 KB' }];
