-- Return to a clean, seeded state in about five seconds.
--
-- Districts, shelters, resources, population cells and pincodes survive, so a
-- re-run needs no re-seed. That is the whole point: being able to reset in
-- seconds is what makes ten rehearsals possible instead of three.
--
-- Deliberately DELETE in foreign-key order rather than TRUNCATE ... CASCADE.
-- CASCADE on `incidents` also truncates every table holding an FK to it — which
-- includes `resources` via committed_incident_id — and would silently wipe the
-- unit roster mid-rehearsal. Learned the hard way.

BEGIN;

-- Release the FK from resources -> incidents before deleting incidents.
UPDATE resources SET committed_incident_id = NULL;

DELETE FROM assignments;
DELETE FROM solver_runs;
DELETE FROM audit_log;
DELETE FROM reports;
DELETE FROM incidents;
DELETE FROM alerts;

-- Incident numbers restart at 1 so "#42" means the same thing every rehearsal.
ALTER SEQUENCE incidents_id_seq RESTART WITH 1;
ALTER SEQUENCE assignments_id_seq RESTART WITH 1;

-- Units go home and go idle; shelters empty and reopen.
UPDATE resources SET status = 'idle', load = 0, current_geom = home_geom;
UPDATE shelters SET occupancy = 0, status = 'open';

COMMIT;

SELECT 'seed intact: '
       || (SELECT COUNT(*) FROM resources) || ' units, '
       || (SELECT COUNT(*) FROM shelters)  || ' shelters, '
       || (SELECT COUNT(*) FROM population_cells) || ' population cells'
       AS reset_result;
