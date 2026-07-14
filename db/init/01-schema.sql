-- BD semilla para desarrollo y pruebas de pg-diagrammer.
-- Cubre: schemas múltiples, FK simple, FK compuesta, tabla puente (N:M),
-- relación 1:1, self-reference, vista y comentarios.

CREATE SCHEMA ventas;
CREATE SCHEMA inventario;

-- === inventario ===
CREATE TABLE inventario.categorias (
    id          serial PRIMARY KEY,
    nombre      text NOT NULL UNIQUE,
    padre_id    integer REFERENCES inventario.categorias (id) -- self-reference
);
COMMENT ON TABLE inventario.categorias IS 'Categorías de productos (jerárquicas)';

CREATE TABLE inventario.productos (
    id           serial PRIMARY KEY,
    sku          text NOT NULL UNIQUE,
    nombre       text NOT NULL,
    precio       numeric(10,2) NOT NULL CHECK (precio >= 0),
    categoria_id integer NOT NULL REFERENCES inventario.categorias (id)
);

CREATE TABLE inventario.fichas_tecnicas (      -- relación 1:1 con productos
    producto_id integer PRIMARY KEY REFERENCES inventario.productos (id) ON DELETE CASCADE,
    peso_kg     numeric(8,3),
    dimensiones text,
    detalle     jsonb
);

-- === ventas ===
CREATE TABLE ventas.clientes (
    id     serial PRIMARY KEY,
    email  text NOT NULL UNIQUE,
    nombre text NOT NULL
);

CREATE TABLE ventas.pedidos (
    id         serial,
    anio       integer NOT NULL,
    cliente_id integer NOT NULL REFERENCES ventas.clientes (id),
    fecha      timestamptz NOT NULL DEFAULT now(),
    estado     text NOT NULL DEFAULT 'nuevo',
    PRIMARY KEY (id, anio)                     -- PK compuesta
);

CREATE TABLE ventas.pedido_items (             -- tabla puente con FK compuesta
    pedido_id   integer NOT NULL,
    pedido_anio integer NOT NULL,
    producto_id integer NOT NULL REFERENCES inventario.productos (id),
    cantidad    integer NOT NULL CHECK (cantidad > 0),
    precio_unit numeric(10,2) NOT NULL,
    PRIMARY KEY (pedido_id, pedido_anio, producto_id),
    FOREIGN KEY (pedido_id, pedido_anio) REFERENCES ventas.pedidos (id, anio) ON DELETE CASCADE
);

CREATE INDEX idx_pedidos_cliente ON ventas.pedidos (cliente_id);
CREATE INDEX idx_items_producto  ON ventas.pedido_items (producto_id);

CREATE VIEW ventas.v_resumen_pedidos AS
SELECT p.id, p.anio, c.nombre AS cliente, count(i.producto_id) AS lineas
FROM ventas.pedidos p
JOIN ventas.clientes c ON c.id = p.cliente_id
LEFT JOIN ventas.pedido_items i ON (i.pedido_id, i.pedido_anio) = (p.id, p.anio)
GROUP BY p.id, p.anio, c.nombre;

-- Datos mínimos
INSERT INTO inventario.categorias (nombre) VALUES ('Electrónica'), ('Hogar');
INSERT INTO inventario.productos (sku, nombre, precio, categoria_id)
VALUES ('SKU-001', 'Teclado', 25.50, 1), ('SKU-002', 'Lámpara', 12.00, 2);
INSERT INTO inventario.fichas_tecnicas (producto_id, peso_kg) VALUES (1, 0.8);
INSERT INTO ventas.clientes (email, nombre) VALUES ('ana@example.com', 'Ana');
INSERT INTO ventas.pedidos (id, anio, cliente_id) VALUES (1, 2026, 1);
INSERT INTO ventas.pedido_items VALUES (1, 2026, 1, 2, 25.50);

-- Rutinas de ejemplo (para "funciones que usan la tabla")
CREATE FUNCTION ventas.total_pedido(p_id integer, p_anio integer) RETURNS numeric AS $$
  SELECT coalesce(sum(cantidad * precio_unit), 0)
  FROM ventas.pedido_items
  WHERE pedido_id = p_id AND pedido_anio = p_anio;
$$ LANGUAGE sql;

CREATE PROCEDURE ventas.cerrar_pedido(p_id integer, p_anio integer) AS $$
BEGIN
  UPDATE ventas.pedidos SET estado = 'cerrado' WHERE id = p_id AND anio = p_anio;
END;
$$ LANGUAGE plpgsql;

-- Vista sobre vista (para diagramas de vistas anidadas)
CREATE VIEW ventas.v_resumen_ext AS
 SELECT r.id, r.anio, r.cliente, p.nombre AS producto
   FROM ventas.v_resumen_pedidos r
     LEFT JOIN inventario.productos p ON p.id = r.id;
